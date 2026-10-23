export interface BuilderSubmitValidationError {
    message?: string;
    type?: string;
}

export interface BuilderSubmitValidationState {
    isValid: boolean;
    values: Record<string, unknown>;
    errors: Record<string, BuilderSubmitValidationError>;
}