import { test } from "node:test";
import assert from "node:assert/strict";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { ResultCards } from "../components/ask/result-cards";
import type { CourtResult } from "../lib/ui-results";

const result: CourtResult = {
  kind: "court_availability", version: 1, id: "court-test", source: "Court Finder", truncated: false,
  venues: [{ name: "Example Centre", operator: "Operator", address: "London", date: "Sun, Oct 25",
    distance_miles: null, price_from_gbp: 12.8, last_refreshed: "", stale: true,
    slots: [{ start: "17:00", end: "18:00", price_gbp: null, booking_url: "https://booking.example.com/1" }] }],
};

test("OpenUI preserves slots with nullable prices and distances, with an explicit provider link", () => {
  const html = renderToStaticMarkup(createElement(ResultCards, { result }));
  assert.match(html, /17:00 – 18:00/);
  assert.match(html, /From £12.80/);
  assert.match(html, /href="https:\/\/booking.example.com\/1"/);
  assert.match(html, /May be stale/);
});

test("unknown prices remain unknown and malformed or unsafe results do not render", () => {
  const unknown = { ...result, venues: [{ ...result.venues[0], price_from_gbp: null,
    slots: [{ ...result.venues[0].slots[0], booking_url: null }] }] };
  const html = renderToStaticMarkup(createElement(ResultCards, { result: unknown }));
  assert.match(html, /17:00 – 18:00/);
  assert.match(html, /Price on provider site/);
  assert.doesNotMatch(html, /View booking/);
  const unsafe = { ...result, venues: [{ ...result.venues[0],
    slots: [{ ...result.venues[0].slots[0], booking_url: "javascript:alert(1)" }] }] };
  assert.equal(renderToStaticMarkup(createElement(ResultCards, { result: unsafe })), "");
});
