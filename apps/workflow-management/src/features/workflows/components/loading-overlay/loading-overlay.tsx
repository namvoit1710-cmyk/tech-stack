import { cn } from "@ldc/ui"

const LoadingOverlay = ({ isLoading }: { isLoading: boolean }) => {
    return (
        <div className={cn(
            "absolute bottom-0 right-0 w-full h-full bg-white/30 backdrop-blur-xs z-30",
            "flex items-center justify-center",
            "transition-all duration-300 ease-in-out",
            "origin-center",
            !isLoading && "scale-0 opacity-0",
            !!isLoading && "scale-100 opacity-100"
        )}>
            <div className="w-12 h-12 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" />
        </div>

    )
}

export default LoadingOverlay