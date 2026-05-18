using System.Text.Json;

namespace TradePro.Api.Data.Stores;

/// <summary>
/// Internal helpers used across the Postgres-backed stores. None of
/// these are interesting on their own; they exist to avoid sprinkling
/// the same five-line JsonElement-to-string dance across nine files.
/// </summary>
internal static class JsonbHelpers
{
    private static readonly JsonSerializerOptions CompactWriteOpts = new()
    {
        WriteIndented = false,
        DefaultIgnoreCondition =
            System.Text.Json.Serialization.JsonIgnoreCondition.WhenWritingNull,
    };

    /// <summary>Serialize a JsonElement to JSON text suitable for a
    /// JSONB column. We pass values as strings + cast to jsonb in the
    /// SQL (`@payload::jsonb`) rather than relying on Npgsql's type
    /// inference — the cast keeps the wire format obvious.</summary>
    public static string ToJsonb(JsonElement el) =>
        el.ValueKind == JsonValueKind.Undefined ? "null" : el.GetRawText();

    public static string ToJsonb<T>(T value) where T : class =>
        JsonSerializer.Serialize(value, CompactWriteOpts);

    /// <summary>Parse a JSONB string back into a JsonElement. The
    /// returned element is owned by a JsonDocument that the caller
    /// must NOT dispose if they want to keep the element alive; we
    /// CloneRoot into a long-lived element here so callers can hold
    /// references without worrying.</summary>
    public static JsonElement FromJsonb(string text)
    {
        using var doc = JsonDocument.Parse(text);
        return doc.RootElement.Clone();
    }

    public static T? FromJsonb<T>(string text) where T : class =>
        JsonSerializer.Deserialize<T>(text, CompactWriteOpts);

    public static string? ReadString(JsonElement el, string key)
        => el.ValueKind == JsonValueKind.Object
           && el.TryGetProperty(key, out var v)
           && v.ValueKind == JsonValueKind.String
            ? v.GetString()
            : null;

    public static int ReadInt(JsonElement el, string key)
        => el.ValueKind == JsonValueKind.Object
           && el.TryGetProperty(key, out var v)
           && v.ValueKind == JsonValueKind.Number
           && v.TryGetInt32(out var n)
            ? n
            : 0;

    public static int? ReadIntOrNull(JsonElement el, string key)
        => el.ValueKind == JsonValueKind.Object
           && el.TryGetProperty(key, out var v)
           && v.ValueKind == JsonValueKind.Number
           && v.TryGetInt32(out var n)
            ? n
            : null;

    public static double? ReadDoubleOrNull(JsonElement el, string key)
        => el.ValueKind == JsonValueKind.Object
           && el.TryGetProperty(key, out var v)
           && v.ValueKind == JsonValueKind.Number
            ? v.GetDouble()
            : null;

    public static DateTime? ReadDateOrNull(JsonElement el, string key)
    {
        if (el.ValueKind != JsonValueKind.Object) return null;
        if (!el.TryGetProperty(key, out var v) || v.ValueKind != JsonValueKind.String) return null;
        var s = v.GetString();
        if (string.IsNullOrEmpty(s)) return null;
        return DateTime.TryParse(s, null,
            System.Globalization.DateTimeStyles.AdjustToUniversal | System.Globalization.DateTimeStyles.AssumeUniversal,
            out var dt) ? dt : null;
    }

    public static string[] ReadStringArray(JsonElement el, string key)
    {
        if (el.ValueKind != JsonValueKind.Object) return Array.Empty<string>();
        if (!el.TryGetProperty(key, out var v) || v.ValueKind != JsonValueKind.Array) return Array.Empty<string>();
        var list = new List<string>(v.GetArrayLength());
        foreach (var item in v.EnumerateArray())
            if (item.ValueKind == JsonValueKind.String) list.Add(item.GetString()!);
        return list.ToArray();
    }

    public static int ReadArrayLength(JsonElement el, string key)
        => el.ValueKind == JsonValueKind.Object
           && el.TryGetProperty(key, out var v)
           && v.ValueKind == JsonValueKind.Array
            ? v.GetArrayLength()
            : 0;
}
