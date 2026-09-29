import { useEffect, useState } from "react";

/** Six pages. Mission control explains the project at a glance; the well twin is the core working screen. */
export type FieldTab = "overview" | "plan" | "maintenance";
export type Route =
  | { page: "home" }
  | { page: "well"; id?: string }
  | { page: "field"; tab?: FieldTab }
  | { page: "optimize"; id?: string; tab?: "optimize" | "whatif"; autorun?: boolean }
  | { page: "actions" }
  | { page: "data"; tab?: "replay" | "models" | "assumptions" };

export function parseHash(hash: string): Route {
  const parts = hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  switch (parts[0]) {
    case "well":
      return { page: "well", id: parts[1] };
    case "field":
      return { page: "field", tab: parts[1] === "plan" || parts[1] === "maintenance" ? parts[1] : "overview" };
    case "optimize":
      return { page: "optimize", id: parts[1], tab: parts[2] === "whatif" ? "whatif" : "optimize", autorun: parts.includes("autorun") };
    case "whatif": // legacy link
      return { page: "optimize", id: parts[1], tab: "whatif" };
    case "actions":
    case "alerts": // legacy link
      return { page: "actions" };
    case "data":
      return { page: "data", tab: parts[1] === "models" || parts[1] === "assumptions" ? parts[1] : "replay" };
    case "models": // legacy links
      return { page: "data", tab: "models" };
    case "about":
    case "history":
      return { page: "data", tab: "assumptions" };
    default:
      return { page: "home" };
  }
}

export function href(r: Route): string {
  switch (r.page) {
    case "home":
      return "#/";
    case "well":
      return r.id ? `#/well/${r.id}` : "#/well";
    case "field":
      return `#/field${r.tab && r.tab !== "overview" ? `/${r.tab}` : ""}`;
    case "optimize":
      return `#/optimize${r.id ? `/${r.id}` : ""}${r.tab === "whatif" ? "/whatif" : ""}`;
    case "data":
      return `#/data${r.tab && r.tab !== "replay" ? `/${r.tab}` : ""}`;
    default:
      return `#/${r.page}`;
  }
}

export function useRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parseHash(location.hash));
  useEffect(() => {
    const on = () => setRoute(parseHash(location.hash));
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return route;
}

export function go(r: Route) {
  location.hash = href(r);
}
