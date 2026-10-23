// Popup wrapper around CredentialAddForm (T7.2). Shared by the node-config credential
// picker ("Setup credential" button) and the /credentials manager page ("Add credential"
// button), so there is exactly one add-credential surface. On success it bubbles the new
// id up and closes; secrets stay inside the form's POST to the vault (never in node state).
import { useLanguage } from "@/hooks/use-language"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@ldc/ui/components/dialog"
import { CredentialAddForm } from "./credential-add-form"

interface ICredentialSetupDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onCreated: (credentialId: string) => void
  provider?: string
  kinds?: string[]
  title?: string
}

export const CredentialSetupDialog = ({
  open,
  onOpenChange,
  onCreated,
  provider,
  kinds,
  title,
}: ICredentialSetupDialogProps) => {
  const { t } = useLanguage()
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{title ?? t("nodes.credential.setup_title")}</DialogTitle>
        </DialogHeader>
        <CredentialAddForm
          provider={provider}
          kinds={kinds}
          onCreated={id => {
            onCreated(id)
            onOpenChange(false)
          }}
          onCancel={() => onOpenChange(false)}
        />
      </DialogContent>
    </Dialog>
  )
}

CredentialSetupDialog.displayName = "CredentialSetupDialog"
