/** Client-side hints mirroring the API password policy (services/api security.password_problems; the API remains the authority). */
export const PASSWORD_MIN = 10;

export function passwordHints(pw: string): string[] {
  const out: string[] = [];
  if (pw.length < PASSWORD_MIN) out.push(`At least ${PASSWORD_MIN} characters`);
  if (pw.toLowerCase() === pw || pw.toUpperCase() === pw) out.push("Upper and lower case");
  if (!/\d/.test(pw)) out.push("At least one digit");
  return out;
}

export function isEmail(v: string): boolean {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v);
}
