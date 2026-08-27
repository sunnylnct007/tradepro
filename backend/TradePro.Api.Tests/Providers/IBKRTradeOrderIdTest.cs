using TradePro.Api.Providers.IBKR;
using Xunit;

namespace TradePro.Api.Tests.Providers;

/// <summary>
/// An execution must carry the order it belongs to.
///
/// 25 Aug 2026. The OMS could never attach a real fill price to an order,
/// because the two halves were never joined: executions
/// (/iserver/account/trades) carry the PRICE, the orders blotter carries the
/// ID, and ParseTrades was dropping the order id IBKR sends on the execution.
///
/// The visible symptom was six IBKR_PAPER orders recorded FILLED at price 0
/// between 29 July and 20 August, and nine more stuck in SUBMITTED for weeks —
/// which makes forward-test gates F2, F3 and F4 uncomputable, since you cannot
/// measure slippage against zero.
/// </summary>
public class IBKRTradeOrderIdTest
{
    /// <summary>
    /// THE REAL PAYLOAD. Captured verbatim from /iserver/account/trades on
    /// 27 Aug 2026 for probe order 1069512750.
    ///
    /// Every other test in this file was written from ASSUMED field names, and
    /// they all passed while production parsed a null order id on every single
    /// execution -- because IBKR sends `order_id`, which none of them covered,
    /// and sends it as a NUMBER rather than a string. Tests built from guesses
    /// confirm the guess, not the integration.
    ///
    /// Anything changing this parser must keep this exact body passing.
    /// </summary>
    [Fact]
    public void The_real_IBKR_execution_payload_parses_completely()
    {
        var json = """
        [{"execution_id":"00025b45.6a96ea93.01.01","symbol":"KO","supports_tax_opt":"1",
          "side":"B","order_description":"Bot 1 @ 89.36 on ARCA",
          "trade_time":"20260827-14:03:18","trade_time_r":1787839398000,
          "size":1.0,"price":"89.36","exchange":"ARCA","commission":"0.9",
          "net_amount":89.36,"account":"DUP656969","accountCode":"DUP656969",
          "company_name":"COCA-COLA CO/THE","contract_description_1":"KO",
          "sec_type":"STK","listing_exchange":"NYSE","conid":8894,
          "position":"2","clearing_id":"IB","order_id":1069512750}]
        """;
        var t = Assert.Single(IBKRResponseParser.ParseTrades(json));
        Assert.Equal("1069512750", t.OrderId);   // number in JSON, string here
        Assert.Equal(89.36m, t.Price);           // string in JSON, decimal here
        Assert.Equal(1m, t.Size);
        Assert.Equal("DUP656969", t.Account);
        Assert.Equal("00025b45.6a96ea93.01.01", t.ExecId);
    }

    /// <summary>The blotter's first answer is a placeholder, and it says so.
    /// Reading it as fact is why the OMS was blind for two months.</summary>
    [Fact]
    public void An_unprimed_blotter_snapshot_is_empty_and_flags_itself()
    {
        Assert.Empty(IBKRResponseParser.ParseLiveOrders("""{"orders":[],"snapshot":false}"""));

        var primed = """
        {"orders":[{"acct":"DUP656969","conid":8894,"orderId":1069512750,
          "ticker":"KO","secType":"STK","remainingQuantity":0.0,"filledQuantity":1.0,
          "totalSize":1.0,"status":"Filled","avgPrice":"89.36","orderType":"Market",
          "side":"BUY"}],"snapshot":true}
        """;
        var o = Assert.Single(IBKRResponseParser.ParseLiveOrders(primed));
        Assert.Equal("1069512750", o.OrderId);
    }

    [Fact]
    public void Execution_carries_the_order_id_under_order_ref()
    {
        var json = """
        [{"symbol":"KO","side":"B","size":1,"price":58.42,
          "trade_time":"20260825-16:31:02","execution_id":"0000e0d5.68abc",
          "account":"DUP656969","order_ref":"1904007755"}]
        """;
        var t = Assert.Single(IBKRResponseParser.ParseTrades(json));
        Assert.Equal("1904007755", t.OrderId);
        Assert.Equal(58.42m, t.Price);
        Assert.Equal(1m, t.Size);
    }

    [Theory]
    [InlineData("order_ref")]
    [InlineData("orderId")]
    [InlineData("ibOrderId")]
    public void The_order_id_is_read_under_any_of_the_three_names_IBKR_uses(string field)
    {
        // IBKR has carried this under different names by endpoint version, and
        // reading only one of them is indistinguishable from the field being
        // absent — which is exactly how this went unnoticed.
        var json = $$"""
        [{"symbol":"KO","side":"B","size":2,"price":58.5,"{{field}}":"777"}]
        """;
        var t = Assert.Single(IBKRResponseParser.ParseTrades(json));
        Assert.Equal("777", t.OrderId);
    }

    [Fact]
    public void An_execution_with_no_order_id_parses_rather_than_throwing()
    {
        // It must still parse — the ledger path uses these executions without
        // needing the id. It simply cannot be reconciled to an OMS order, and
        // the reconciler skips it rather than guessing.
        var json = """[{"symbol":"KO","side":"B","size":1,"price":58.42}]""";
        var t = Assert.Single(IBKRResponseParser.ParseTrades(json));
        Assert.Null(t.OrderId);
        Assert.Equal(58.42m, t.Price);
    }

    [Fact]
    public void A_zero_price_execution_is_parsed_but_carries_zero_for_the_caller_to_reject()
    {
        // The parser does not editorialise; the reconciler and the OMS both
        // refuse a zero-price fill, and RecordFillAsync now throws on one.
        var json = """[{"symbol":"KO","side":"B","size":1,"price":0,"order_ref":"1"}]""";
        var t = Assert.Single(IBKRResponseParser.ParseTrades(json));
        Assert.Equal(0m, t.Price);
    }
}

/// <summary>
/// IBKR sends execution price and size as STRINGS. Parsing them with a
/// number-only reader turned every real fill into a fill at price zero.
/// </summary>
public class IBKRTradeStringPriceTest
{
    [Fact]
    public void A_string_price_is_read_as_a_number_not_dropped_to_zero()
    {
        // The exact shape IBKR returned for a live probe order on 25 Aug 2026.
        var json = """
        [{"symbol":"KO","side":"B","size":"1","price":"58.42",
          "trade_time":"20260825-16:25:00","exec_id":"00025b45.6a945749.01.01"}]
        """;
        var t = Assert.Single(IBKRResponseParser.ParseTrades(json));
        Assert.Equal(58.42m, t.Price);
        Assert.Equal(1m, t.Size);
    }

    [Fact]
    public void A_numeric_price_still_works()
    {
        var json = """[{"symbol":"KO","side":"B","size":1,"price":58.42}]""";
        var t = Assert.Single(IBKRResponseParser.ParseTrades(json));
        Assert.Equal(58.42m, t.Price);
    }

    [Fact]
    public void A_halted_market_marker_on_the_price_string_is_stripped()
    {
        // IBKR prefixes 'C' or 'H' onto string values around halts. Left in,
        // decimal.TryParse fails and the fill silently becomes zero again.
        var json = """[{"symbol":"KO","side":"B","size":"1","price":"C58.42"}]""";
        var t = Assert.Single(IBKRResponseParser.ParseTrades(json));
        Assert.Equal(58.42m, t.Price);
    }
}
