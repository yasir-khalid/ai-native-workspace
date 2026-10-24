import { z } from "zod/v4";

export function publicBookingUrl(value: string) {
  try {
    const url = new URL(value);
    return url.protocol === "https:" && !url.username && !url.password && url.hostname.includes(".") &&
      !/(^localhost$|\.(local|internal|localhost)$|^(127\.|10\.|192\.168\.|169\.254\.|0\.)|^172\.(1[6-9]|2\d|3[01])\.)/i.test(url.hostname);
  } catch { return false; }
}
const slotSchema = z.object({
  start: z.string().min(1).max(40), end: z.string().min(1).max(40),
  price_gbp: z.number().finite().nonnegative().nullable(),
  booking_url: z.string().max(2000).refine(publicBookingUrl).nullable(),
});
export const courtResultSchema = z.object({
  kind: z.literal("court_availability"), version: z.literal(1), id: z.string().max(100),
  source: z.string().max(120), truncated: z.boolean(),
  venues: z.array(z.object({
    name: z.string().min(1).max(250), operator: z.string().max(250), address: z.string().max(500),
    date: z.string().max(80), distance_miles: z.number().finite().nonnegative().nullable(),
    price_from_gbp: z.number().finite().nonnegative().nullable().optional(),
    last_refreshed: z.string().max(80), stale: z.boolean(), slots: z.array(slotSchema).min(1).max(40),
  })).max(20),
});
export type CourtResult = z.infer<typeof courtResultSchema>;
