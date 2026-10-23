import { describe, expect, it } from "@rstest/core"
import {
  buildDefaultValues,
  buildSubmitMapping,
  flattenForSubmit,
  parseFormValuesPath,
  stripActionButtons,
} from "./credential-form-mapping"

// A trimmed smtp-basic-shaped schema: root object "credentialForm" with a submit_credential
// action whose api_call body declares the flat mapping via ${formValues.<path>}.
const smtpSchema = {
  fields: [
    {
      key: "credentialForm",
      outputType: "object",
      fieldConfig: {
        fieldWrapper: "DivWrapper",
        wrapperProps: {
          actions: [
            {
              action: "submit_credential",
              steps: [
                {
                  type: "api_call",
                  body: {
                    typeId: "smtp-basic",
                    displayName: "${formValues.displayName}",
                    formData: {
                      host: "${formValues.tabs.server.host}",
                      port: "${formValues.tabs.server.port}",
                      security: "${formValues.tabs.server.security}",
                      username: "${formValues.tabs.credentials.username}",
                      password: "${formValues.tabs.credentials.password}",
                    },
                  },
                },
              ],
            },
          ],
        },
      },
      fields: [],
    },
  ],
} as never

describe("parseFormValuesPath", () => {
  it("extracts a dotted path and drops the formValues head", () => {
    expect(parseFormValuesPath("${formValues.tabs.server.host}")).toEqual(["tabs", "server", "host"])
    expect(parseFormValuesPath("${formValues.displayName}")).toEqual(["displayName"])
  })
  it("returns null for non-references", () => {
    expect(parseFormValuesPath("literal")).toBeNull()
    expect(parseFormValuesPath(123)).toBeNull()
    expect(parseFormValuesPath("${other.thing}")).toBeNull()
  })
})

describe("buildSubmitMapping", () => {
  it("reads the flat mapping from the submit_credential api_call", () => {
    const m = buildSubmitMapping(smtpSchema)
    expect(m).not.toBeNull()
    expect(m!.rootKey).toBe("credentialForm")
    expect(m!.formData.host).toEqual(["tabs", "server", "host"])
    expect(m!.formData.password).toEqual(["tabs", "credentials", "password"])
    expect(m!.displayNamePath).toEqual(["displayName"])
  })
  it("returns null when there is no schema/submit action", () => {
    expect(buildSubmitMapping(null)).toBeNull()
  })
})

describe("prefill ↔ flatten round-trip", () => {
  it("nests stored non-secret values into Builder defaults (secret stays blank)", () => {
    const m = buildSubmitMapping(smtpSchema)!
    const defaults = buildDefaultValues(
      m,
      { host: "smtp.office365.com", port: 587, security: "starttls", username: "noreply@company.com" },
      "Office365 SMTP",
    )
    expect(defaults).toEqual({
      credentialForm: {
        displayName: "Office365 SMTP",
        tabs: {
          server: { host: "smtp.office365.com", port: 587, security: "starttls" },
          credentials: { username: "noreply@company.com" },
        },
      },
    })
    // password path is absent → renders blank, forcing re-entry.
    expect((defaults.credentialForm as never as { tabs: { credentials: { password?: unknown } } }).tabs.credentials.password).toBeUndefined()
  })

  it("flattens the Builder's nested values back to {displayName, formData}", () => {
    const m = buildSubmitMapping(smtpSchema)!
    const builderValues = {
      credentialForm: {
        displayName: "Renamed",
        tabs: {
          server: { host: "smtp.example.com", port: 465, security: "ssl" },
          credentials: { username: "u@x.com", password: "re-entered" },
        },
      },
    }
    expect(flattenForSubmit(builderValues, m)).toEqual({
      displayName: "Renamed",
      formData: {
        host: "smtp.example.com",
        port: 465,
        security: "ssl",
        username: "u@x.com",
        password: "re-entered",
      },
    })
  })

  it("omits missing keys rather than emitting undefined", () => {
    const m = buildSubmitMapping(smtpSchema)!
    const { formData } = flattenForSubmit(
      { credentialForm: { tabs: { server: { host: "h" } } } },
      m,
    )
    expect(formData).toEqual({ host: "h" })
    expect("password" in formData).toBe(false)
  })
})

describe("stripActionButtons", () => {
  const schemaWithButtons = {
    fields: [
      {
        key: "credentialForm",
        outputType: "object",
        fieldConfig: {
          fieldWrapper: "DivWrapper",
          wrapperProps: {
            actions: [
              {
                action: "submit_credential",
                steps: [{ type: "api_call", body: { displayName: "${formValues.displayName}", formData: { host: "${formValues.host}" } } }],
              },
            ],
          },
        },
        fields: [
          { key: "displayName", fieldConfig: { fieldControl: "InputControl" } },
          { key: "host", fieldConfig: { fieldControl: "InputControl" } },
          { key: "btnTest", fieldConfig: { fieldControl: "ButtonControl" } },
          { key: "btnSave", fieldConfig: { fieldControl: "ButtonControl" } },
        ],
      },
    ],
  } as never

  it("removes ButtonControl fields but keeps data fields", () => {
    const out = stripActionButtons(schemaWithButtons) as never as {
      fields: { fields: { key: string; fieldConfig: { fieldControl: string } }[] }[]
    }
    const keys = out.fields[0]!.fields.map(f => f.key)
    expect(keys).toEqual(["displayName", "host"])
    expect(keys).not.toContain("btnTest")
    expect(keys).not.toContain("btnSave")
  })

  it("preserves the submit_credential action (used to build the flatten mapping)", () => {
    const out = stripActionButtons(schemaWithButtons) as never
    // The mapping reads the action off wrapperProps, not the pruned button fields.
    expect(buildSubmitMapping(out)).not.toBeNull()
  })

  it("does not mutate the input schema", () => {
    const before = JSON.stringify(schemaWithButtons)
    stripActionButtons(schemaWithButtons)
    expect(JSON.stringify(schemaWithButtons)).toBe(before)
  })

  it("returns null for null", () => {
    expect(stripActionButtons(null)).toBeNull()
  })
})
