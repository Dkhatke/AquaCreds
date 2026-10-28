import { describe, expect, it } from "vitest";
import { formatDate, formatPercent, humanize, shortHash } from "./format";
import { proxyable } from "./proxy-policy";
import { safeNext } from "./safe-next";

describe("proxyable", () => {
  it("allows API v1 resources", () => {
    expect(proxyable("GET", ["v1", "projects"])).toBe(true);
    expect(proxyable("POST", ["v1", "alerts", "x", "acknowledge"])).toBe(true);
  });
  it("keeps token-bearing auth and demo endpoints behind the dedicated handlers", () => {
    for (const p of ["login", "refresh", "register", "logout"]) {
      expect(proxyable("POST", ["v1", "auth", p])).toBe(false);
    }
    expect(proxyable("POST", ["v1", "auth", "mfa", "verify"])).toBe(false);
    expect(proxyable("POST", ["v1", "demo", "session"])).toBe(false);
    expect(proxyable("GET", ["v1", "auth", "me"])).toBe(true);
    expect(proxyable("POST", ["v1", "auth", "me"])).toBe(false);
  });
  it("refuses traversal and non-API paths", () => {
    expect(proxyable("GET", ["v1", "..", "readyz"])).toBe(false);
    expect(proxyable("GET", ["v2", "x"])).toBe(false);
    expect(proxyable("GET", ["v1"])).toBe(false);
  });
});

describe("safeNext", () => {
  it("only follows console paths", () => {
    expect(safeNext("/console/projects/1/monitoring")).toBe("/console/projects/1/monitoring");
    expect(safeNext("https://evil.example")).toBe("/console");
    expect(safeNext("//evil.example")).toBe("/console");
    expect(safeNext("/console/login")).toBe("/console");
    expect(safeNext(null)).toBe("/console");
  });
});

describe("format", () => {
  it("formats dates in UTC", () => {
    expect(formatDate("2024-12-14")).toBe("14 Dec 2024");
    expect(formatDate("2024-12-14T23:30:00Z")).toBe("14 Dec 2024");
  });
  it("formats percents, hashes and identifiers", () => {
    expect(formatPercent(0.9451)).toBe("94.5 %");
    expect(shortHash("0123456789abcdef0123")).toBe("012345…0123");
    expect(humanize("tidal_flat")).toBe("Tidal flat");
  });
});
