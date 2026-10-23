/**
 * Recursively removes internal fields (prefixed with __) from form data
 * Used before submitting form values to backend
 * 
 * Internal fields convention:
 * - __selected: Row selection state
 * - __validate_status: Validation status
 * - __validate_message: Validation message
 * - __file_id: Temporary file IDs
 * - Any field starting with __ is considered internal
 */
export const cleanOutput = (data: any): any => {
    // Handle arrays
    if (Array.isArray(data)) {
        return data.map(cleanOutput);
    }
    
    // Handle objects
    if (data && typeof data === "object") {
        return Object.fromEntries(
            Object.entries(data)
                .filter(([key]) => !key.startsWith("__"))  // Remove internal fields
                .map(([key, value]) => [key, cleanOutput(value)])  // Recursively clean nested values
        );
    }
    
    // Return primitives as-is
    return data;
};
