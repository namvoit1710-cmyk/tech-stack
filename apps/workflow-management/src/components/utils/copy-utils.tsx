import { CheckIcon, CopyIcon, Link2Icon, Loader } from "lucide-react";
import { useState } from "react";

interface CopyUtilsProps {
    content: string;
    // SA-2704: optional link glyph (for "copy shareable link") + tooltip. Default
    // stays the plain copy icon so existing call sites are unchanged.
    icon?: "copy" | "link";
    title?: string;
    ariaLabel?: string;
}

const CopyUtils = ({ content, icon = "copy", title, ariaLabel }: CopyUtilsProps) => {
    const [isLoading, setIsLoading] = useState(false);
    const [isCopied, setIsCopied] = useState(false);

    const handleCopy = async () => {
        if (isLoading) return;

        setIsLoading(true);
        try {
            await navigator.clipboard.writeText(content);
            setIsCopied(true);
            setTimeout(() => setIsCopied(false), 1000);
        } catch (error) {
            // SA-2704: clipboard can be blocked (permissions / insecure context).
            // Fall back to a hidden textarea + execCommand so Copy never throws.
            try {
                const ta = document.createElement("textarea");
                ta.value = content;
                ta.style.position = "fixed";
                ta.style.opacity = "0";
                document.body.appendChild(ta);
                ta.focus();
                ta.select();
                document.execCommand("copy");
                document.body.removeChild(ta);
                setIsCopied(true);
                setTimeout(() => setIsCopied(false), 1000);
            } catch (fallbackError) {
                console.error("Failed to copy text: ", error, fallbackError);
            }
        } finally {
            setIsLoading(false);
        }
    }

    const Idle = icon === "link" ? Link2Icon : CopyIcon;

    return (
        <span
            className="ml-2 inline-flex items-center cursor-pointer text-muted-foreground hover:text-primary"
            onClick={handleCopy}
            title={title}
            aria-label={ariaLabel ?? title}
            role="button"
        >
            {!isLoading && !isCopied && <Idle className="size-3" />}
            {isLoading && !isCopied && <Loader className="size-3 animate-spin text-primary" />}
            {!isLoading && isCopied && <CheckIcon className="size-3 text-green-500" />}
        </span>
    )
}

export default CopyUtils;
