// SA-2617 — In-UI User guide (help drawer) for the Environments page.
// A plain-English slide-over that explains the Section / Environment / Setting model
// to a non-technical admin: no code, no jargon. Opened from the "User guide" button
// in the page header. Two-column layout so it fits without scrolling on a laptop.
import {
    Sheet,
    SheetContent,
    SheetDescription,
    SheetHeader,
    SheetTitle,
} from "@ldc/ui/components/sheet";
import type { ReactNode } from "react";

function GuideSection({ title, children }: { title: ReactNode; children: ReactNode }) {
    return (
        <div className="mb-4">
            <h3 className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">{title}</h3>
            {children}
        </div>
    );
}

function Chip({ tone, children }: { tone: "sec" | "env"; children: ReactNode }) {
    const cls = tone === "sec" ? "bg-violet-50 text-violet-600" : "bg-sky-50 text-sky-700";
    return <span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${cls}`}>{children}</span>;
}

export function UserGuideDrawer({ open, onOpenChange }: { open: boolean; onOpenChange: (v: boolean) => void }) {
    return (
        <Sheet open={open} onOpenChange={onOpenChange}>
            <SheetContent side="right" className="flex w-full flex-col gap-0 p-0 sm:max-w-[900px]">
                <SheetHeader className="border-b px-5 py-3.5">
                    <SheetTitle className="flex items-center gap-2">
                        Quick guide
                        <span className="rounded-md border border-violet-200 bg-violet-50 px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wide text-violet-600">
                            plain english
                        </span>
                    </SheetTitle>
                    <SheetDescription>
                        Keep the settings your workflows need in one place — with a separate set of values for each stage
                        (Development, Test, Production).
                    </SheetDescription>
                </SheetHeader>

                <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4 text-[13px] leading-snug text-foreground">
                    <div className="grid grid-cols-1 gap-x-7 sm:grid-cols-2">
                        {/* ---- left column ---- */}
                        <div>
                            <GuideSection title="The 3 pieces">
                                <div className="grid grid-cols-3 gap-2">
                                    <div className="rounded-lg border border-violet-200 bg-violet-50 p-2 text-violet-700">
                                        <div className="text-[12px] font-semibold">Section</div>
                                        <div className="text-[11.5px]">A settings group for one project (e.g. “Payments”).</div>
                                    </div>
                                    <div className="rounded-lg border border-sky-200 bg-sky-50 p-2 text-sky-700">
                                        <div className="text-[12px] font-semibold">Environment</div>
                                        <div className="text-[11.5px]">A stage: Development, Test or Production.</div>
                                    </div>
                                    <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-2 text-emerald-700">
                                        <div className="text-[12px] font-semibold">Setting</div>
                                        <div className="text-[11.5px]">One entry, like a web address or a key.</div>
                                    </div>
                                </div>
                                <p className="mt-2">
                                    Think of the <b>Section</b> as a form that lists the settings, and each <b>Environment</b>{" "}
                                    fills in that same form with its own answers.
                                </p>
                            </GuideSection>

                            <GuideSection title="Set the list once — fill values per stage">
                                <p className="mb-2">
                                    On the Section you list the settings you need. Then, for each stage, you just type that
                                    stage’s values — every stage automatically has the same settings.
                                </p>
                                <div className="flex flex-wrap items-center gap-1.5 rounded-lg border bg-muted/30 px-2.5 py-2 text-[12px]">
                                    <Chip tone="sec">Section: Payments</Chip>
                                    <span>→</span>
                                    <Chip tone="env">Development</Chip>
                                    <Chip tone="env">Test</Chip>
                                    <Chip tone="env">Production</Chip>
                                    <span className="text-muted-foreground">· same list, own values</span>
                                </div>
                            </GuideSection>

                            <GuideSection title="Same setting, different value per stage">
                                <div className="space-y-1 rounded-lg border bg-muted/30 px-2.5 py-2 text-[12px]">
                                    <div>
                                        <span className="font-semibold">Payment service address</span> — Test:{" "}
                                        <i>test-pay.example.com</i> · Production: <i>pay.example.com</i>
                                    </div>
                                    <div>
                                        <span className="font-semibold">API key</span> — a different key for each stage
                                    </div>
                                </div>
                            </GuideSection>

                            <GuideSection title="Secrets stay protected">
                                <p>
                                    Passwords and keys are hidden (shown as <b>••••</b>) and stored securely — never shown on
                                    screen or exported as plain text.
                                </p>
                            </GuideSection>
                        </div>

                        {/* ---- right column ---- */}
                        <div>
                            <GuideSection title="Point a workflow at a Section">
                                <p>
                                    Each workflow uses <b>one</b> Section. When you run it, you choose which stage to use — e.g.
                                    run against <b>Test</b> first, then <b>Production</b>. A Section can be shared by many
                                    workflows.
                                </p>
                            </GuideSection>

                            <GuideSection title="⚡ Automatic (dynamic) stage — when a workflow calls a sub-flow">
                                <p className="mb-2">
                                    When one workflow <b>calls another workflow</b> (a “sub-flow”), the calling step can choose
                                    which stage the sub-flow runs in <b>automatically</b> — decided at run time from a value in
                                    the data — instead of a fixed stage. So the same sub-flow can run in Test or Production
                                    depending on what the parent passes in.
                                </p>
                                <div className="space-y-2 rounded-lg border border-amber-200 bg-amber-50 px-2.5 py-2 text-[12px] text-amber-900">
                                    <div>
                                        <span className="font-semibold">Set it up:</span> on the step that calls the sub-flow,
                                        switch its stage from <b>“Fixed”</b> to <b>“Automatic”</b> and point it at the value
                                        that decides it.
                                    </div>
                                    <div>
                                        <span className="font-semibold">Example:</span> the parent passes <i>“Production”</i> →
                                        the sub-flow runs in Production; it passes <i>“Test”</i> → the sub-flow runs in Test.
                                    </div>
                                    <div>
                                        <span className="font-semibold">Safety:</span> the chosen stage must belong to the{" "}
                                        <b>sub-flow’s own Section</b>. If it points to a stage that isn’t allowed there, the run
                                        is stopped — never the wrong environment.
                                    </div>
                                </div>
                            </GuideSection>

                            <GuideSection title="Get started">
                                <ol className="text-[12.5px]">
                                    {[
                                        <>Create a <b>Section</b> and list the settings it needs.</>,
                                        <>Add your <b>stages</b> (Development, Test, Production) and fill in each stage’s values.</>,
                                        <>Open a <b>workflow</b> and point it at that Section.</>,
                                        <>Run it and pick the stage you want.</>,
                                    ].map((step, i) => (
                                        <li key={i} className="flex gap-2.5 border-t border-dashed py-1.5 first:border-t-0">
                                            <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-primary text-[11px] font-bold text-primary-foreground">
                                                {i + 1}
                                            </span>
                                            <span>{step}</span>
                                        </li>
                                    ))}
                                </ol>
                            </GuideSection>
                        </div>
                    </div>
                </div>
            </SheetContent>
        </Sheet>
    );
}

export default UserGuideDrawer;
