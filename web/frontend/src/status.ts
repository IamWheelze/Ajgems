// Review-status styling (FRD 10.3): colour-blind-safe Okabe-Ito colours, always paired with an
// outline style and an icon, never colour alone. Keep in step with the QGIS styles.

export type StatusStyle = { label: string; colour: string; dash: number[] | null; icon: string };

export const STATUS: Record<string, StatusStyle> = {
  unreviewed: { label: "Unreviewed", colour: "#BBBBBB", dash: [2, 2], icon: "○" },
  confirmed: { label: "Confirmed", colour: "#0072B2", dash: null, icon: "✓" },
  edited: { label: "Edited", colour: "#56B4E9", dash: null, icon: "✎" },
  rejected: { label: "Rejected", colour: "#D55E00", dash: [0.5, 1.5], icon: "✕" },
  uncertain: { label: "Uncertain", colour: "#F0E442", dash: [4, 2], icon: "?" },
  field_check_requested: { label: "Field check requested", colour: "#CC79A7", dash: [4, 2, 1, 2], icon: "⚑" },
  field_confirmed: { label: "Field confirmed", colour: "#009E73", dash: null, icon: "✔" },
  field_rejected: { label: "Field rejected", colour: "#E69F00", dash: [0.5, 1.5], icon: "✖" },
  released: { label: "Released", colour: "#009E73", dash: null, icon: "★" },
};

export const BAND_LABEL: Record<string, string> = { high: "High", medium: "Medium", low: "Low" };

export const ACTION_LABEL: Record<string, string> = {
  confirm: "Confirm", reject: "Reject", uncertain: "Uncertain", request_field_check: "Request field check",
  field_confirm: "Field confirm", field_reject: "Field reject", release: "Release", edit: "Edit",
};

// Keyboard shortcuts in the review workspace.
export const SHORTCUTS: Record<string, string> = { c: "confirm", r: "reject", u: "uncertain", f: "request_field_check" };

export const human = (code: string) => code.replace(/_/g, " ");
