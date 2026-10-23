import type { i18n } from "@ldc/i18n"
import en from "./locales/en.json"

export const RETE_EDITOR_I18N_NAMESPACE = "rete-editor"

const resources = { en } as const
export type ReteEditorLocale = keyof typeof resources

export function addReteEditorResources(i18nInstance: i18n) {
  Object.entries(resources).forEach(([lang, messages]) => {
    i18nInstance.addResourceBundle(
      lang,
      RETE_EDITOR_I18N_NAMESPACE,
      messages,
      true,
      false
    )
  })
}