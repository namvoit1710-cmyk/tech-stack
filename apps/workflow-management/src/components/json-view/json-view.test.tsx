import { beforeEach, describe, expect, it, rs } from "@rstest/core"
import { fireEvent, render, screen } from "@testing-library/react"
import type { ReactNode } from "react"
import JsonView from "./json-view"

const setDataMock = rs.fn()
let mockKeyResult: { keyName: string | number; keys?: Array<string | number> } = {
    keyName: "total",
    keys: ["preview", "total"],
}

rs.mock("@uiw/react-json-view", () => {
    const MockJsonView = ({ children }: { children?: ReactNode }) => <div>{children}</div>

    MockJsonView.KeyName = ({
        render,
    }: {
        render: (
            props: Record<string, unknown>,
            result: { keyName: string | number; keys?: Array<string | number> }
        ) => ReactNode
    }) => render({}, mockKeyResult)

    return {
        default: MockJsonView,
    }
})

describe("JsonView", () => {
    beforeEach(() => {
        setDataMock.mockClear()
        mockKeyResult = {
            keyName: "total",
            keys: ["preview", "total"],
        }
    })

    it("uses the top-level display path for nested draggable keys", () => {
        render(
            <JsonView
                value={{ preview: { total: 2 } }}
                draggableKeys
                raw={{
                    preview: {
                        displayPath: "artifact.preview",
                    },
                }}
                replacePathKey="displayPath"
            />,
        )

        fireEvent.dragStart(screen.getByText("total"), {
            dataTransfer: {
                setData: setDataMock,
            },
        })

        expect(setDataMock).toHaveBeenCalledTimes(1)
        expect(setDataMock).toHaveBeenCalledWith("text/plain", "artifact.preview.total")
    })
})