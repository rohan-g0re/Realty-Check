"""Ready-made look() questions the harness calls."""
from __future__ import annotations

from .look import look


def count_floors(image_paths: list[str]) -> dict:
    """Count above-ground stories of the main building in elevation or angled aerial photos."""
    question = (
        "How many above-ground stories (floors) does the main building in these images have? "
        "For angled aerial photos, count the horizontal rows of windows or balconies on one facade "
        "from the ground to the roof line. Count from the bottom: the ground floor (storefronts, arches, "
        "lobby or parking podium) is story 1, then each window or balcony row above it is one more story; "
        "never count the same level twice, and say in how_counted whether the ground floor is a podium. "
        "Do not count roof equipment, parapets or rooftop structures."
    )
    fields = {
        "stories": "integer: number of above-ground floors of the main building",
        "how_counted": "string: which facade and rows you counted, and whether a ground-floor retail/parking podium was included",
    }
    return look(image_paths, question, fields)


def read_floorplan(image_paths: list[str]) -> dict:
    """Read bedroom and bathroom counts and room labels from a floor plan drawing."""
    question = (
        "This is a floor plan of one apartment unit. Read the room labels. Count bedrooms (rooms "
        "labelled bedroom, BR, master, or similar) and bathrooms (bath, BA, powder counts as one). "
        "A den, study, office or flex room is not a bedroom."
    )
    fields = {
        "bedrooms": "integer: number of bedrooms",
        "bathrooms": "integer: number of bathrooms",
        "has_den": "true/false: whether there is a den, study, office or flex room",
        "room_labels": "list of strings: every room label written on the plan, as written",
    }
    return look(image_paths, question, fields)


def check_room(image_paths: list[str], claim_text: str) -> dict:
    """Check what room the photos show and whether they support a listing claim about it."""
    question = (
        f'A rental listing claims: "{claim_text}". Look at these photos of the room. What kind of room '
        "is it, does it have a window to the outside, and do the photos support the claim?"
    )
    fields = {
        "room_type": "string: kind of room shown, e.g. bedroom, den, living room, kitchen, bathroom",
        "has_window": "true/false: whether the room has at least one window to the outside",
        "supports_claim": 'string: "yes", "no" or "unclear"',
    }
    return look(image_paths, question, fields)


PRESENCE_FIELD = ('object {"presence": "seen" | "not_seen" | "unclear", "confidence": number 0-1 for this '
                  'feature, "image": number of the image that shows it best (1-based), or null}')

SETTINGS = {
    "aerial": (
        "Image 1 is a top-down aerial photo, about 120 m across, centred on one apartment property; the other "
        "images are angled aerial views of the same spot. For each feature below, decide whether it is present "
        "on THIS property: the large building at the centre of image 1, its roofs, courtyards and its own lot. "
        "Ignore neighbouring buildings, streets and lots."
    ),
    "scan": (
        "These images come from a 3D scan of one apartment: a schematic floor plan and 2x2 grids of the four "
        "views around the camera at a few spots. For each feature below, decide whether it is present in this "
        "apartment."
    ),
}


def find_features(image_paths: list[str], features: dict[str, str], setting: str = "aerial") -> dict:
    """Presence check for several features in one call. features: {key: plain description}.
    answer[key] = {"presence": "seen"|"not_seen"|"unclear", "confidence": 0-1, "image": n or None}.
    Presence only: never use this to count anything."""
    intro = SETTINGS.get(setting, SETTINGS["aerial"])
    listing = "\n".join(f"- {k}: {d}" for k, d in features.items())
    question = (
        f"{intro}\n\nFeatures:\n{listing}\n\n"
        "For each feature: seen = clearly visible in at least one image; not_seen = the place where it would "
        "be is clearly visible and it is not there; unclear = anything else (hidden, indoors, too small, out of "
        "frame, or you are not sure). Do not guess from what such buildings usually have."
    )
    return look(image_paths, question, {k: PRESENCE_FIELD for k in features})
