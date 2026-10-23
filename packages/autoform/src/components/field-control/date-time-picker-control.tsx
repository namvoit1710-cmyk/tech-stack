import { DateTimePicker } from '@ldc/ui/blocks/date-time-picker/date-time-picker'
import type { DateTimePickerMode } from '@ldc/ui/blocks/date-time-picker/date-time-picker'
import type { TimeMode } from '@ldc/ui/blocks/time-picker/time-picker'
import React from 'react'
import type { FieldComponentProps } from '../../types/schema'

export interface DateTimePickerControlProps extends FieldComponentProps {
  mode?: DateTimePickerMode
  timeMode?: TimeMode
  minDateTime?: string
  maxDateTime?: string
  placeholder?: string
}

const DateTimePickerControl = React.forwardRef<HTMLDivElement, DateTimePickerControlProps>(
  (props, _ref) => {
    const { name, value, onChange, error, field, mode, timeMode, minDateTime, maxDateTime, placeholder, disabled } = props

    const resolvedMode: DateTimePickerMode =
      mode ?? field?.fieldConfig?.controlProps?.mode ?? 'single'

    const resolvedTimeMode: TimeMode =
      timeMode ?? field?.fieldConfig?.controlProps?.timeMode ?? '24h'

    const resolvedPlaceholder: string | undefined =
      placeholder ?? field?.fieldConfig?.controlProps?.placeholder

    const resolvedMinDateTime: string | undefined =
      minDateTime ?? field?.fieldConfig?.controlProps?.minDateTime

    const resolvedMaxDateTime: string | undefined =
      maxDateTime ?? field?.fieldConfig?.controlProps?.maxDateTime

    if (resolvedMode === 'range') {
      return (
        <DateTimePicker
          mode="range"
          value={value as { from?: string; to?: string } | undefined}
          onChange={(val) => onChange?.({ target: { name, value: val } })}
          placeholder={resolvedPlaceholder}
          disabled={disabled}
          error={error}
          timeMode={resolvedTimeMode}
          minDateTime={resolvedMinDateTime}
          maxDateTime={resolvedMaxDateTime}
        />
      )
    }

    return (
      <DateTimePicker
        value={typeof value === 'string' ? value : undefined}
        onChange={(val) => onChange?.({ target: { name, value: val } })}
        placeholder={resolvedPlaceholder}
        disabled={disabled}
        error={error}
        timeMode={resolvedTimeMode}
        minDateTime={resolvedMinDateTime}
        maxDateTime={resolvedMaxDateTime}
      />
    )
  },
)

DateTimePickerControl.displayName = 'DateTimePickerControl'

export default DateTimePickerControl
