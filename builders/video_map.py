"""Map videos to listings by photo-to-video timestamp proximity.

Videos arrive in a shared cloud folder with filenames that match nothing. The
listing they belong to has to be inferred, and the only signal that survives is
*when things were shot*: a listing's video was recorded in the same session as
that listing's photos.

So each video is matched to the listing whose photos were captured nearest in
time. Not to the session:

    Session-level guessing -- "these were all shot on the same day, so
    distribute them across that day's listings" -- produces wrong assignments
    and was explicitly rejected. A day contains many listings; nearness within
    the day is the entire signal, and averaging it away discards it.

Where two listings are close enough that the answer is genuinely ambiguous, the
video goes to a **review file with its candidates** rather than being assigned.
A wrong video on a listing is worse than a missing one: it is a product page
showing a different product, and nobody checks.

A known Google Drive limitation, worth encoding as a check
----------------------------------------------------------
Searching with ``parentId = '<folderID>'`` returns **empty results** for folders
owned by an external account, even when the folder is plainly visible via
``sharedWithMe = true``. Files inside are unreachable via search unless shared
individually or the service account is added as a Viewer on the folder itself.

A zero-result folder query therefore has two very different causes, and
:func:`diagnose_empty_folder` names the likely one instead of reporting the
useless "no files found".
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Two candidate listings whose nearest photos are within this many seconds of
#: each other are treated as ambiguous. Roughly the length of a photo session
#: for one product: closer than this and the ordering is noise.
AMBIGUITY_WINDOW = 300

#: Beyond this, a video is not from the same session as any listing's photos.
#: Six hours: a shoot day, not a calendar day.
MAX_DISTANCE = 6 * 3600


@dataclass
class Candidate:
    sku: str
    distance: float


@dataclass
class VideoMatch:
    video: str
    sku: str = ""
    distance: float = 0.0
    candidates: list[Candidate] = field(default_factory=list)
    ambiguous: bool = False
    unmatched: bool = False


def nearest_photo_distance(video_time: float, photo_times: list[float]) -> float:
    """Seconds from a video to the closest photo of one listing."""
    if not photo_times:
        return float("inf")
    return min(abs(video_time - t) for t in photo_times)


def map_videos(video_times: dict[str, float],
               listing_photo_times: dict[str, list[float]],
               ambiguity_window: float = AMBIGUITY_WINDOW,
               max_distance: float = MAX_DISTANCE) -> list[VideoMatch]:
    """Assign each video to a listing, or flag it for review.

    Both inputs are plain timestamps, so this is pure: reading EXIF or file
    mtimes happens in the caller and does not need to be mocked to test the
    matching.
    """
    matches = []
    for video, video_time in sorted(video_times.items()):
        scored = sorted(
            (Candidate(sku, nearest_photo_distance(video_time, times))
             for sku, times in listing_photo_times.items()),
            key=lambda c: c.distance,
        )
        scored = [c for c in scored if c.distance <= max_distance]

        if not scored:
            matches.append(VideoMatch(video=video, unmatched=True))
            continue

        best = scored[0]
        rivals = [c for c in scored[1:]
                  if c.distance - best.distance <= ambiguity_window]

        if rivals:
            matches.append(VideoMatch(
                video=video, distance=best.distance,
                candidates=[best] + rivals, ambiguous=True))
        else:
            matches.append(VideoMatch(video=video, sku=best.sku,
                                      distance=best.distance,
                                      candidates=[best]))
    return matches


def write_review_file(matches: list[VideoMatch], path: str) -> int:
    """Write the ambiguous and unmatched videos with their candidates."""
    needing_review = [m for m in matches if m.ambiguous or m.unmatched]
    with open(path, "w", encoding="utf-8") as handle:
        if not needing_review:
            handle.write("Every video mapped to exactly one listing.\n")
            return 0
        handle.write(
            "Videos that could not be assigned automatically.\n"
            "Timestamp proximity did not separate these; assigning one anyway\n"
            "would put a different product's video on a listing, which nobody\n"
            "checks. Pick by hand.\n\n")
        for match in needing_review:
            if match.unmatched:
                handle.write("%s\n    no listing within the time window\n\n"
                             % match.video)
                continue
            handle.write("%s\n" % match.video)
            for candidate in match.candidates:
                handle.write("    %-24s %.0fs away\n"
                             % (candidate.sku, candidate.distance))
            handle.write("\n")
    return len(needing_review)


def summary(matches: list[VideoMatch]) -> str:
    mapped = sum(1 for m in matches if m.sku)
    ambiguous = sum(1 for m in matches if m.ambiguous)
    unmatched = sum(1 for m in matches if m.unmatched)
    return "\n".join([
        "", "=== Video mapping ===",
        "videos:     %d" % len(matches),
        "mapped:     %d" % mapped,
        "ambiguous:  %d  (in the review file, not assigned)" % ambiguous,
        "unmatched:  %d  (no listing within the time window)" % unmatched,
    ])


def diagnose_empty_folder(folder_visible: bool, result_count: int) -> str | None:
    """Name the real cause of a zero-result Drive folder query.

    Returns None when there is nothing to diagnose.
    """
    if result_count or not folder_visible:
        return None
    return (
        "The folder is visible but a parentId query returned no files.\n"
        "This is a known Google Drive behaviour, not an empty folder: searching\n"
        "with parentId = '<folderID>' returns nothing for folders owned by an\n"
        "external account, even when the folder shows up under\n"
        "sharedWithMe = true.\n\n"
        "Fix it one of two ways:\n"
        "  - have the owner add this service account as a Viewer on the folder\n"
        "    itself, not just share its contents, or\n"
        "  - have the files shared individually.\n\n"
        "Retrying the query will not help."
    )
