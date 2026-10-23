import { useEffect, useRef } from "react";

const DROPPABLE_TAGS = new Set(["INPUT", "TEXTAREA"]);

const isDroppableTarget = (tagName: string): boolean =>
  DROPPABLE_TAGS.has(tagName);

const applyDropHighlight = (el: HTMLElement) => {
  el.style.border = "2px solid #3b82f6";
  el.style.borderRadius = "4px";
  el.style.transition = "border 0.05s ease";
};

const removeDropHighlight = (el: HTMLElement) => {
  el.style.border = "";
  el.style.borderRadius = "";
  el.style.transition = "";
};

const appendAndTriggerReact = (
  el: HTMLInputElement | HTMLTextAreaElement,
  text: string
) => {
  const nativeProto =
    el.tagName === "TEXTAREA"
      ? window.HTMLTextAreaElement.prototype
      : window.HTMLInputElement.prototype;

  const nativeSetter = Object.getOwnPropertyDescriptor(
    nativeProto,
    "value"
  )?.set;

  if (nativeSetter) {
    nativeSetter.call(el, (el.value ?? "") + text);
  } else {
    el.value = (el.value ?? "") + text;
  }

  el.dispatchEvent(new Event("input", { bubbles: true }));
};

const useFormDropZone = () => {
    const ref = useRef<HTMLDivElement | null>(null);

    useEffect(() => {
        const container = ref.current;
        if (!container) return;

        let highlightedTarget: HTMLElement | null = null;

        const clearHighlight = () => {
            if (highlightedTarget) {
                removeDropHighlight(highlightedTarget);
                highlightedTarget = null;
            }
        };

        const handleDragOver = (e: DragEvent) => {
            if (!e.dataTransfer?.types.includes("text/plain")) return;

            const target = e.target as HTMLElement;
            if (!isDroppableTarget(target.tagName)) {
                clearHighlight();
                return;
            }

            e.preventDefault();
            e.dataTransfer.dropEffect = "copy";

            if (highlightedTarget !== target) {
                clearHighlight();
                applyDropHighlight(target);
                highlightedTarget = target;
            }
        };

        const handleDragLeave = (e: DragEvent) => {
            const target = e.target as HTMLElement;
            if (!isDroppableTarget(target.tagName)) return;

            const relatedTarget = e.relatedTarget as HTMLElement | null;

            if (relatedTarget) {
                if (target.contains(relatedTarget)) return;
            } else {
                const rect = target.getBoundingClientRect();
                if (
                    e.clientX >= rect.left &&
                    e.clientX <= rect.right &&
                    e.clientY >= rect.top &&
                    e.clientY <= rect.bottom
                )
                    return;
            }

            if (highlightedTarget === target) {
                clearHighlight();
            }
        };

        const handleDragEnd = () => clearHighlight();

        const handleDrop = (e: DragEvent) => {
            e.preventDefault();
            clearHighlight();

            const target = e.target as HTMLElement;
            if (!isDroppableTarget(target.tagName)) return;

            const pathKey = e.dataTransfer?.getData("text/plain");
            if (!pathKey) return;

            appendAndTriggerReact(
                target as HTMLInputElement | HTMLTextAreaElement,
                pathKey
            );
        };

        container.addEventListener("dragover", handleDragOver);
        container.addEventListener("dragleave", handleDragLeave);
        container.addEventListener("drop", handleDrop);
        document.addEventListener("dragend", handleDragEnd);

        return () => {
            container.removeEventListener("dragover", handleDragOver);
            container.removeEventListener("dragleave", handleDragLeave);
            container.removeEventListener("drop", handleDrop);
            document.removeEventListener("dragend", handleDragEnd);
        };
    }, []);

  return ref;
};

export default useFormDropZone;