import { useLanguage } from "@/hooks/use-language";
import { Badge } from "@ldc/ui/components/badge";
import { Popover, PopoverContent, PopoverTrigger } from "@ldc/ui/components/popover";

interface WorkerAppNodesPopoverProps {
    count: number;
    nodes: string[];
}

/** The node types are catalog information, not fleet health — a chip, not rows.
 *  22 jira node types rendered as 22 "workers" was the bug this epic exists to
 *  fix; the full catalog lives on the Node Definitions page. Here they collapse
 *  into a single count chip that opens a popover on demand.
 *
 *  `nodes` comes off the app summary row, NOT off the per-app instance query:
 *  that query only runs once the row is expanded, so the popover rendered an
 *  empty box on every collapsed row — which is every row, until the user
 *  expands one. */
const WorkerAppNodesPopover = ({ count, nodes }: WorkerAppNodesPopoverProps) => {
    const { t } = useLanguage();
    return (
        <Popover>
            <PopoverTrigger asChild>
                <button type="button" aria-label={t("workers.col.nodes")}>
                    <Badge variant="outline" className="text-sm tabular-nums">
                        {count}
                    </Badge>
                </button>
            </PopoverTrigger>
            <PopoverContent className="max-h-64 w-64 overflow-auto">
                {nodes.length === 0 ? (
                    <p className="text-muted-foreground text-sm">{t("workers.no_node_types")}</p>
                ) : (
                    <ul className="space-y-1 text-sm">
                        {nodes.map((n) => (
                            <li key={n} className="text-muted-foreground font-mono">
                                {n}
                            </li>
                        ))}
                    </ul>
                )}
            </PopoverContent>
        </Popover>
    );
};

export default WorkerAppNodesPopover;
