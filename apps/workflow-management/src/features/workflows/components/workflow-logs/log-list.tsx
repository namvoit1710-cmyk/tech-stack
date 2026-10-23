import type { IWorkflowLogs } from "@/features/workflows/types/workflow-log";
import { useEffect, useRef } from "react";
import LogItem from "./log-item";

interface LogListProps {
    logs: IWorkflowLogs[];
}

const LogList = ({ logs }: LogListProps) => {
    const topRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        topRef.current?.scrollIntoView({ behavior: "smooth" });
    }, [logs.length]);

    return (
        <div className="flex flex-col gap-0.5 py-1">
            <div ref={topRef} />

            {logs.map((log) => (
                <LogItem key={log.id} log={log} />
            ))}
        </div>
    );
};

export default LogList;
