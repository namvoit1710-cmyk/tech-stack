import { useLanguage } from "@/hooks/use-language";

const LogEmptyState = () => {
    const { t } = useLanguage();

    return (
        <div className="flex items-center justify-center h-full text-sm text-gray-400 select-none">
            {t("no_logs")}
        </div>
    );
};

export default LogEmptyState;
