export type ServerOptionMode = "default" | "odata";
export interface IResponseMapping {
    dataPath?: string;
    totalPath?: string;
}

export interface IODataConfig {
    defaultFilter?: string;
    defaultOrderBy?: string;
    defaultSelect?: string;
    searchField?: string;
}

export interface IServerOptionsConfig {
    endpoint: string;
    service: string;
    dependencies?: string[];
    /** Legacy REST source only — the Gateway path has no client-side default/odata sub-mode. */
    mode?: ServerOptionMode;
    method?: "GET" | "POST";
    /** Static request body for POST sources (e.g. a gateway function-invoke payload). */
    body?: Record<string, unknown>;
    params?: Record<string, string | number | boolean>;
    /** Static params merged into every request. May include `contextValues` as a URL-encoded JSON string mapping API key → form field name. Pagination params ($top/$skip/$count) always override these. */
    queryParams?: Record<string, string | number | boolean>;
    responseMapping?: IResponseMapping;
    /** Legacy REST source only — the Gateway path has no client-side default/odata sub-mode. */
    odataConfig?: IODataConfig;
    /** Gateway typeahead only (Combobox / SelectTable) — the typed term rides `args[searchArgKey]`. Default `"$search"`. */
    searchArgKey?: string;
    /**
     * Gateway paged sources only — the upstream's own names for the page args.
     *
     * The gateway forwards ONLY args whose keys are declared in the function's
     * `input_params`, and the declared key IS the upstream query name (there is no
     * rename step). Hard-coding `$top`/`$skip`/`$count` therefore pages correctly only
     * against an upstream that literally speaks OData; anything else silently ignores
     * them and every page returns the same rows. Binding them per function fixes that.
     *
     * Omit a key to keep the OData default; set it to `""` to not send that arg at all
     * (an upstream with no total/count param should not receive one).
     */
    pageArgKeys?: { top?: string; skip?: string; count?: string };
}
