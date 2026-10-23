import { TimePicker } from '@ldc/ui/blocks/time-picker/time-picker'
import type { TimeMode } from '@ldc/ui/blocks/time-picker/time-picker'
import React from 'react'
import type { FieldComponentProps } from '../../types/schema'

export interface TimePickerControlProps extends FieldComponentProps {
  mode?: TimeMode
  minTime?: string
  maxTime?: string
  placeholder?: string
}

const TimePickerControl = React.forwardRef<HTMLDivElement, TimePickerControlProps>(
  (props, _ref) => {
    const { name, value, onChange, error, field, mode, minTime, maxTime, placeholder, disabled } = props

    const resolvedMode: TimeMode =
      mode ?? field?.fieldConfig?.controlProps?.mode ?? '24h'

    const resolvedPlaceholder: string | undefined =
      placeholder ?? field?.fieldConfig?.controlProps?.placeholder

    const resolvedMinTime: string | undefined =
      minTime ?? field?.fieldConfig?.controlProps?.minTime

    const resolvedMaxTime: string | undefined =
      maxTime ?? field?.fieldConfig?.controlProps?.maxTime

    return (
      <TimePicker
        mode={resolvedMode}
        value={value as string | undefined}
        onChange={(val) => onChange?.({ target: { name, value: val } })}
        minTime={resolvedMinTime}
        maxTime={resolvedMaxTime}
        placeholder={resolvedPlaceholder}
        disabled={disabled}
        error={error}
      />
    )
  },
)

TimePickerControl.displayName = 'TimePickerControl'

export default TimePickerControl
