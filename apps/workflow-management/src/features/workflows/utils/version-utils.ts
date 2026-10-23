export const VERSION_LABEL_LENGTH = 8;

export function formatVersionLabel(version: string | null | undefined): string {
    if (!version) return "—";
    return version.slice(0, VERSION_LABEL_LENGTH);
}

export function formatShortDate(dateStr: string): string {
    return new Intl.DateTimeFormat(undefined, {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
    }).format(new Date(dateStr));
}
