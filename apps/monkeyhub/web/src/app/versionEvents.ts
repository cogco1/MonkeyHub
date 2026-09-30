import type { StudioEventDto } from "../api/generated";

/**
 * The Studio events after which Modeling reads its versions again (artifacts, working
 * copies, the working draft): a model was registered, an option was added, a candidate
 * succeeded. Its own module so the event stream's browser test runs this exact rule.
 */
export function refreshesVersions(event: Pick<StudioEventDto, "type">): boolean {
  return event.type === "model_asset.registered" || event.type === "working_copy.option_added" ||
    event.type === "candidate.succeeded";
}
