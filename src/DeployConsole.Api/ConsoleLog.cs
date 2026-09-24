namespace DeployConsole;

public static partial class ConsoleLog
{
    [LoggerMessage(Level = LogLevel.Error, Message = "部署调度失败：{Type}")]
    public static partial void Scheduler(ILogger logger, string type);
    [LoggerMessage(Level = LogLevel.Error, Message = "请求处理失败：{Type}")]
    public static partial void Request(ILogger logger, string type);
    [LoggerMessage(Level = LogLevel.Warning, Message = "操作失败 {JobId}：{Type}")]
    public static partial void Operation(ILogger logger, Guid jobId, string type);
}
