using System.Text.Json;
using Microsoft.Extensions.Options;

namespace DeployConsole;

// The lock protects both state publication and the atomic disk replacement.
public sealed class StateStore : IDisposable
{
    public static readonly JsonSerializerOptions Json = new(JsonSerializerDefaults.Web);
    private readonly SemaphoreSlim gate = new(1);
    private readonly string file;
    private readonly FileStream instanceLock;
    private ConsoleState state;

    public StateStore(IOptions<ConsoleOptions> options)
    {
        var directory = Path.GetFullPath(options.Value.DataDirectory);
        Directory.CreateDirectory(directory);
        instanceLock = new FileStream(Path.Combine(directory, "instance.lock"), FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None);
        file = Path.Combine(directory, "state.json");
        state = File.Exists(file) ? JsonSerializer.Deserialize<ConsoleState>(File.ReadAllText(file), Json) ?? throw new InvalidDataException("Invalid console state") : new();
    }

    public async Task<T> ReadAsync<T>(Func<ConsoleState, T> read)
    {
        await gate.WaitAsync();
        try { return read(Clone(state)); }
        finally { gate.Release(); }
    }

    public async Task<T> WriteAsync<T>(Func<ConsoleState, T> write)
    {
        await gate.WaitAsync();
        try
        {
            var next = Clone(state);
            var result = write(next);
            if (JsonSerializer.Serialize(next, Json) == JsonSerializer.Serialize(state, Json)) return result;
            var temporary = file + "." + Guid.NewGuid().ToString("N") + ".tmp";
            try
            {
                await using (var stream = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None, 4096, FileOptions.WriteThrough))
                {
                    await JsonSerializer.SerializeAsync(stream, next, Json);
                    await stream.FlushAsync();
                    stream.Flush(true);
                }
                File.Move(temporary, file, true);
                state = next;
                return result;
            }
            finally { if (File.Exists(temporary)) File.Delete(temporary); }
        }
        finally { gate.Release(); }
    }
    private static ConsoleState Clone(ConsoleState source) => JsonSerializer.Deserialize<ConsoleState>(JsonSerializer.Serialize(source, Json), Json)!;
    public void Dispose() { instanceLock.Dispose(); gate.Dispose(); }
}
