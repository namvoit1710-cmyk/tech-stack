import {
    Tooltip,
    TooltipContent,
    TooltipProvider,
    TooltipTrigger,
} from "@ldc/ui/components/tooltip";
import { format, formatDistanceToNow } from "date-fns";
import { enGB } from "date-fns/locale";

interface IRelativeTimeProps {
    value?: string | null;
    /** Optional leading word, e.g. "updated" → "updated 2 days ago". */
    prefix?: string;
    className?: string;
}

// Relative time ("2 days ago") for scanning, with the full timestamp on hover.
export function RelativeTime({ value, prefix, className }: IRelativeTimeProps) {
    if (!value) return <span className={className}>—</span>;

    const date = new Date(value);
    const relative = formatDistanceToNow(date, { addSuffix: true });
    const absolute = format(date, "PPpp", { locale: enGB });

    return (
        <TooltipProvider>
            <Tooltip>
                <TooltipTrigger asChild>
                    <span className={className}>
                        {prefix ? `${prefix} ` : ""}{relative}
                    </span>
                </TooltipTrigger>
                <TooltipContent>{absolute}</TooltipContent>
            </Tooltip>
        </TooltipProvider>
    );
}

export default RelativeTime;
