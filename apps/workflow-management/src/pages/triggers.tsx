// Triggers management route (/triggers, SA-2059 B9) — Settings → Triggers.
// Inbound webhook registrations + their deliveries. The workflow side (Event
// trigger nodes) is configured in the builder; this page is the ingress side.
import Page from "@/components/containers/page";
import TriggerRegistrationsSettings from "@/features/settings/trigger-registrations-settings";
import { Button } from "@ldc/ui/components/button";
import { PlusIcon } from "lucide-react";
import { useState } from "react";

const TriggersPage = () => {
  const [addOpen, setAddOpen] = useState(false);
  return (
    <Page>
      <Page.Header
        title="Triggers"
        description="Inbound webhook registrations. One per source; the URL fans out to any workflow subscribing to its event topic."
        actions={
          <Button data-testid="registrations-add-open" onClick={() => setAddOpen(true)}>
            <PlusIcon className="size-4" /> New registration
          </Button>
        }
      />
      <TriggerRegistrationsSettings addOpen={addOpen} onAddOpenChange={setAddOpen} />
    </Page>
  );
};

export default TriggersPage;
