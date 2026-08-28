export function logError(error: unknown): void {
  if (!process.env.SHIVA_INK_DEBUG_ERRORS) {
    return
  }

  console.error(error)
}
