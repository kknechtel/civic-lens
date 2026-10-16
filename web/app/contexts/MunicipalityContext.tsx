"use client";

/**
 * The municipality this deployment serves — names, entities (library groups),
 * branding and feature flags — loaded once from GET /api/municipality, which
 * reads municipalities/<slug>/municipality.yaml on the API.
 *
 * Nothing in the web client should hardcode a town; read it from here.
 */
import { createContext, useContext, useEffect, useState, ReactNode } from "react";

export type EntityKind = "municipal" | "school" | "county" | "state" | "regional" | "special_district" | "other";

export interface MunicipalityEntity {
  key: string;
  label: string;
  short: string;
  kind: EntityKind;
}

export interface Municipality {
  slug: string;
  name: string;
  short_name: string;
  government_type: string;
  state: string;
  county: string;
  timezone: string;
  fiscal_year: { start_month: number; school_start_month: number };
  branding: {
    app_name: string; tagline: string; theme_color: string; public_origin: string; contact_email: string;
    banner_url?: string; banner_alt?: string;
  };
  features: { financial: boolean; meetings: boolean; events: boolean; presentations: boolean; contracts: boolean; alerts: boolean; integrity: boolean };
  entities: MunicipalityEntity[];
  records_law: { name: string; short: string; citation: string } | null;
}

// Shown for the instant before the profile loads (and if the API is down) —
// deliberately generic so no town's name ever flashes on another town's site.
export const FALLBACK_MUNICIPALITY: Municipality = {
  slug: "",
  name: "",
  short_name: "",
  government_type: "municipality",
  state: "",
  county: "",
  timezone: "America/New_York",
  fiscal_year: { start_month: 1, school_start_month: 7 },
  branding: {
    app_name: process.env.NEXT_PUBLIC_APP_NAME || "Civic Lens",
    tagline: "Document Intelligence, Events & Local Business",
    theme_color: "#385854",
    public_origin: "",
    contact_email: "",
  },
  features: { financial: true, meetings: true, events: true, presentations: true, contracts: true, alerts: true, integrity: true },
  entities: [],
  records_law: null,
};

const MunicipalityContext = createContext<{ municipality: Municipality; loaded: boolean }>({
  municipality: FALLBACK_MUNICIPALITY,
  loaded: false,
});

export function MunicipalityProvider({ children }: { children: ReactNode }) {
  const [municipality, setMunicipality] = useState<Municipality>(FALLBACK_MUNICIPALITY);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/municipality")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((m: Municipality) => {
        if (cancelled) return;
        setMunicipality(m);
        if (typeof document !== "undefined" && m.branding?.app_name) {
          document.title = m.branding.app_name;
        }
      })
      .catch((err) => console.warn("municipality profile unavailable:", err))
      .finally(() => !cancelled && setLoaded(true));
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <MunicipalityContext.Provider value={{ municipality, loaded }}>{children}</MunicipalityContext.Provider>
  );
}

export function useMunicipality(): Municipality {
  return useContext(MunicipalityContext).municipality;
}

export function useMunicipalityLoaded(): boolean {
  return useContext(MunicipalityContext).loaded;
}

/** First entity of a given kind (e.g. the school district), if any. */
export function entityOfKind(m: Municipality, kind: EntityKind): MunicipalityEntity | undefined {
  return m.entities.find((e) => e.kind === kind);
}
