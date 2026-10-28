import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LoginForm } from "./LoginForm";

const replace = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace, refresh: vi.fn() }) }));

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => {
  replace.mockReset();
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => vi.unstubAllGlobals());

describe("LoginForm", () => {
  it("asks for a TOTP code when the API returns an MFA challenge, then signs in", async () => {
    const user = userEvent.setup();
    fetchMock
      .mockResolvedValueOnce(json(200, { mfa_required: true, expires_in: 300 }))
      .mockResolvedValueOnce(json(200, { ok: true, recovery_codes_remaining: null }));
    render(<LoginForm next="/console/projects/p1/monitoring" expired={false} />);

    await user.type(screen.getByLabelText("Email"), "verifier@example.org");
    await user.type(screen.getByLabelText("Password"), "correct horse");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    const code = await screen.findByLabelText("Authentication code");
    const first = fetchMock.mock.calls[0]?.[0] as Request;
    expect(first.url).toMatch(/\/bff\/auth\/login$/);
    expect(first.headers.get("x-aquacreds-csrf")).toBe("1");
    expect(replace).not.toHaveBeenCalled();

    await user.type(code, "123 456");
    await user.click(screen.getByRole("button", { name: "Verify" }));
    const second = fetchMock.mock.calls[1]?.[0] as Request;
    expect(await second.json()).toEqual({ code: "123456" });
    expect(replace).toHaveBeenCalledWith("/console/projects/p1/monitoring");
  });

  it("shows the API's error message", async () => {
    const user = userEvent.setup();
    fetchMock.mockResolvedValueOnce(json(401, { detail: "Invalid email or password" }));
    render(<LoginForm next={null} expired />);
    expect(screen.getByText(/Your session ended/)).toBeInTheDocument();
    await user.type(screen.getByLabelText("Email"), "a@example.org");
    await user.type(screen.getByLabelText("Password"), "nope");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid email or password");
  });
});
