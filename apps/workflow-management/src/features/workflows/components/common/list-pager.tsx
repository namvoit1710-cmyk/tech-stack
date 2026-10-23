import { cn } from "@ldc/ui";
import {
    Pagination,
    PaginationContent,
    PaginationItem,
    PaginationNext,
    PaginationPrevious,
} from "@ldc/ui/components/pagination";

export function totalPages(total: number, pageSize: number): number {
    return Math.max(1, Math.ceil((total || 0) / pageSize));
}

interface IListPagerProps {
    page: number;
    total: number;
    pageSize: number;
    onChange: (page: number) => void;
    label: string;
}

// Prev / "Page X of Y" / Next. Replaces the table's built-in pager for card lists.
export function ListPager({ page, total, pageSize, onChange, label }: IListPagerProps) {
    const pages = totalPages(total, pageSize);
    if (pages <= 1) return null;

    return (
        <Pagination className="justify-end">
            <PaginationContent>
                <PaginationItem>
                    <PaginationPrevious
                        href="#"
                        aria-disabled={page <= 1}
                        className={cn(page <= 1 && "pointer-events-none opacity-50")}
                        onClick={(e) => {
                            e.preventDefault();
                            if (page > 1) onChange(page - 1);
                        }}
                    />
                </PaginationItem>
                <PaginationItem>
                    <span className="px-2 text-sm text-muted-foreground whitespace-nowrap">{label}</span>
                </PaginationItem>
                <PaginationItem>
                    <PaginationNext
                        href="#"
                        aria-disabled={page >= pages}
                        className={cn(page >= pages && "pointer-events-none opacity-50")}
                        onClick={(e) => {
                            e.preventDefault();
                            if (page < pages) onChange(page + 1);
                        }}
                    />
                </PaginationItem>
            </PaginationContent>
        </Pagination>
    );
}

export default ListPager;
