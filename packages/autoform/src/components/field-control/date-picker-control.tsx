import type { DatePickerMode } from '@ldc/ui/blocks/date-picker/date-picker';
import { DatePicker } from '@ldc/ui/blocks/date-picker/date-picker';
import React from 'react';
import type { FieldComponentProps } from '../../types/schema';

export interface DatePickerControlProps extends FieldComponentProps {
  mode?: DatePickerMode
  placeholder?: string
}

const DatePickerControl = React.forwardRef<HTMLButtonElement, DatePickerControlProps>(
  (props, _ref) => {
    const { name, value, onChange, error, field, mode, placeholder, disabled } = props

    const resolvedMode =
      mode ??
      field?.fieldConfig?.controlProps?.mode ??
      'single'

    const resolvedPlaceholder: string | undefined =
      placeholder ?? field?.fieldConfig?.controlProps?.placeholder

    if (resolvedMode === 'range') {
      return (
        <DatePicker
          mode="range"
          value={value as { from?: string; to?: string } | undefined}
          onChange={(val) => onChange?.({ target: { name, value: val } })}
          placeholder={resolvedPlaceholder}
          disabled={disabled}
          error={error}
        />
      )
    }

    return (
      <DatePicker
        mode="single"
        value={value as string | undefined}
        onChange={(val) => onChange?.({ target: { name, value: val } })}
        placeholder={resolvedPlaceholder}
        disabled={disabled}
        error={error}
      />
    )
  },
)

DatePickerControl.displayName = 'DatePickerControl'

export default DatePickerControl
