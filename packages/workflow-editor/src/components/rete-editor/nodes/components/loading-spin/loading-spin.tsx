import { cn } from "@ldc/ui";

interface ILoadingSpinProps {
    className?: string,
}

const LoadingSpin = ({ className }: ILoadingSpinProps) => {
    return (
        <div
            className={cn(
                "size-4 border-2 border-blue-500 border-t-transparent rounded-full animate-spin",
                className
            )}
        />
    )
}

export default LoadingSpin;