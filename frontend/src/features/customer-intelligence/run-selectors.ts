import type { AnyRunStreamEvent } from "./contracts";

export function selectVisibleRunEvents(
  events: AnyRunStreamEvent[],
): AnyRunStreamEvent[] {
  const hasGenericSteps = events.some(
    (event) => event.type === "step_started" || event.type === "step_completed",
  );
  const hasGenericPlan = events.some(
    (event) => event.type === "plan_created" || event.type === "plan_revised",
  );
  const hasGenericValidation = events.some(
    (event) => event.type === "report_validating",
  );

  return events.filter((event) => {
    if (
      hasGenericSteps &&
      (event.type === "tool_started" || event.type === "tool_completed")
    ) {
      return false;
    }
    if (hasGenericPlan && event.type === "plan") return false;
    if (hasGenericValidation && event.type === "validating") return false;
    return true;
  });
}
