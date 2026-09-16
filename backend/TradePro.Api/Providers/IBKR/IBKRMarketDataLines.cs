namespace TradePro.Api.Providers.IBKR;

/// <summary>
/// Capacity manager for the ONE IBKR market-data session.
///
/// WHY THIS EXISTS. Every caller on this desk — the Mac launchd daemons, the
/// Lambda jobs, our own MCP endpoint — goes through this single backend and
/// therefore through a single <see cref="IBKRSessionCache"/>. That part was
/// already right. What was missing is that ONE session is a FINITE resource
/// and nothing was counting.
///
/// /iserver/marketdata/snapshot does not read, it SUBSCRIBES: each conid
/// takes a market-data line and holds it. Before this class the codebase
/// contained not one call to unsubscribe — grep it on b085db4 — so every
/// line we ever opened stayed open until IBKR expired it on its own.
///
/// What that session was asked for on an ordinary afternoon:
///
///   14:12Z  strangle delta solve   25 strikes x 2 legs x 6 markets ~ 300
///   14:15Z  straddle-scan          ~67 names' chains
///   /15min  paper-swing-ibkr       244 symbols
///   /30min  bar-cache-harvest-5m   244 symbols
///
/// Past the cap IBKR does not return an error. It returns EMPTY FIELDS, which
/// arrives at our callers as "no strikes" and "could not read spot" — and
/// because lines age out by themselves it heals with no intervention inside
/// an hour. That is the exact signature of 16 Sep: all six markets dark at
/// 14:12:21Z, the identical call serving 40 legs 45 minutes later, nothing
/// changed in between. It reads as a flaky feed. It is us oversubscribing our
/// own session and never giving the lines back.
///
/// So this class does the two things that were missing: it BOUNDS how many
/// lines can be outstanding at once, and it guarantees the lines a pass
/// opened are RELEASED when that pass ends. Callers take a lease and dispose
/// it; over-budget callers queue rather than pile on.
///
/// NOT a rate limiter. It bounds concurrently-held lines, which is the
/// resource IBKR actually caps. Requests-per-second is a different limit
/// with a different remedy.
/// </summary>
public sealed class IBKRMarketDataLines
{
    private readonly object _gate = new();
    private readonly Queue<Waiter> _waiters = new();
    private readonly int _max;
    private int _inUse;
    private long _grantedTotal;
    private long _queuedTotal;

    private sealed record Waiter(int Count, TaskCompletionSource<bool> Signal);

    /// <param name="maxLines">
    /// Concurrent market-data lines we permit ourselves. Deliberately a
    /// CONFIGURED ceiling and not a probe of IBKR's real cap: the API does
    /// not publish the number, and discovering it empirically means running
    /// the session into the failure we are trying to prevent. Hold it below
    /// where we think the cap is and the question stops mattering.
    /// </param>
    public IBKRMarketDataLines(int maxLines)
    {
        if (maxLines < 1)
            throw new ArgumentOutOfRangeException(
                nameof(maxLines), maxLines,
                "A market-data line budget below 1 would deadlock every snapshot call.");
        _max = maxLines;
    }

    /// <summary>Lines currently leased out.</summary>
    public int InUse { get { lock (_gate) return _inUse; } }

    /// <summary>The configured ceiling.</summary>
    public int Max => _max;

    /// <summary>Callers waiting for capacity right now.</summary>
    public int Waiting { get { lock (_gate) return _waiters.Count; } }

    /// <summary>
    /// Leases granted, and how many of those had to QUEUE first. A rising
    /// queued share is the desk telling us the budget is too small for the
    /// schedule — the honest fix then is to stagger the jobs, not to raise
    /// the ceiling until IBKR starts serving blanks again.
    /// </summary>
    public (long Granted, long Queued) Stats
    {
        get { lock (_gate) return (_grantedTotal, _queuedTotal); }
    }

    /// <summary>
    /// Reserve <paramref name="count"/> lines, waiting if the budget is full.
    ///
    /// A request LARGER than the whole budget is clamped rather than refused.
    /// Refusing would mean a caller whose chunk size someone later raised
    /// above the ceiling fails permanently and silently — the blank-chain
    /// failure we are here to remove, reintroduced from our own side. Clamped,
    /// it runs at reduced parallelism, which is slow and correct.
    /// </summary>
    public async Task AcquireAsync(int count, CancellationToken ct = default)
    {
        if (count <= 0) return;
        var want = Math.Min(count, _max);

        Waiter waiter;
        lock (_gate)
        {
            if (_waiters.Count == 0 && _inUse + want <= _max)
            {
                _inUse += want;
                _grantedTotal++;
                return;
            }
            // Queue even when capacity happens to exist but others are already
            // waiting. Without that check a steady stream of small requests
            // starves a large one indefinitely, and the large one is the
            // strangle placement.
            waiter = new Waiter(want, new TaskCompletionSource<bool>(
                TaskCreationOptions.RunContinuationsAsynchronously));
            _waiters.Enqueue(waiter);
            _queuedTotal++;
        }

        using (ct.Register(() => waiter.Signal.TrySetCanceled(ct)))
        {
            try
            {
                await waiter.Signal.Task.ConfigureAwait(false);
            }
            catch (OperationCanceledException)
            {
                // Cancelled while queued. Drop out of the queue and re-pump:
                // whoever is behind us may now fit. Leaving this out strands
                // the queue behind a caller that has already given up.
                lock (_gate)
                {
                    if (_waiters.Contains(waiter))
                    {
                        var kept = _waiters.Where(w => w != waiter).ToList();
                        _waiters.Clear();
                        foreach (var w in kept) _waiters.Enqueue(w);
                    }
                    else
                    {
                        // We were granted between the cancel and the lock —
                        // the lines are ours and nobody else will free them.
                        _inUse -= want;
                    }
                    PumpLocked();
                }
                throw;
            }
        }
    }

    /// <summary>Hand <paramref name="count"/> lines back.</summary>
    public void Release(int count)
    {
        if (count <= 0) return;
        var give = Math.Min(count, _max);
        lock (_gate)
        {
            _inUse = Math.Max(0, _inUse - give);
            PumpLocked();
        }
    }

    /// <summary>Wake every queued caller that now fits. Caller holds _gate.</summary>
    private void PumpLocked()
    {
        while (_waiters.Count > 0)
        {
            var next = _waiters.Peek();
            if (_inUse + next.Count > _max) break;
            _waiters.Dequeue();
            _inUse += next.Count;
            _grantedTotal++;
            // TrySet: the waiter may already have cancelled, in which case its
            // cancellation path returns these lines.
            if (!next.Signal.TrySetResult(true)) _inUse -= next.Count;
        }
    }
}
