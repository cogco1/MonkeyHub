"""What a message carries besides its words: attached files and registered project pages.

An attached image is checked against the type it declares and a read of an
attachment is bounded. A registered page is bound to the conversation's own
project at exactly the run, digest, revision and page it names before a chat
shows it or discusses it with Render.
"""

from __future__ import annotations

from pathlib import Path
from io import BytesIO

from .. import projects

from ..models import ChatDocument, ChatDocumentRef, ChatRenderContext, HubFailure


_IMAGE_MIMES = {"image/png", "image/jpeg", "image/webp", "image/gif"}


def _verify_image(data: bytes, mime_type: str) -> None:
    from PIL import Image
    try:
        with Image.open(BytesIO(data)) as image:
            if Image.MIME.get(image.format) != mime_type:
                raise ValueError("The image format does not match its MIME type.")
            image.verify()
    except Exception as exc:
        raise HubFailure(422, "CHAT_IMAGE_INVALID", "This attachment is not a valid image of the declared type.") from exc


def _attachment_read_paging(offset: int, limit: int, page: int) -> None:
    if (type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 65536
            or type(page) is not int or page < 1):
        raise HubFailure(422, "CHAT_ATTACHMENT_READ_INVALID", "Use offset >= 0, limit from 1 to 65536, and page >= 1.")


def _project_binding(project_id: str, project_dir: str):
    """The conversation's own project, opened read-only, once its identity is verified."""

    from project_runtime.binding import ProjectBinding

    if projects._project(project_dir) != (project_id, project_dir):
        raise HubFailure(409, "CHAT_PROJECT_MISMATCH", "The conversation's project identity changed.")
    return ProjectBinding.unconfigured(Path(project_dir), project_id=project_id)


def _registered_page(binding, ref: ChatDocumentRef):
    """The registration naming exactly this run, digest, revision (null included) and page, or None.

    Only exact identity answers: a run the project does not have, or one that
    cannot be read, holds no such page.
    """

    from archflow.project.repository import ProjectRepositoryError
    from project_runtime.application.artifacts import list_documents
    from project_runtime.errors import StudioError

    try:
        document = next((row for row in list_documents(binding, ref.runId)
                         if (row.run_id, row.asset_sha256, row.revision_ref) == (ref.runId, ref.assetSha256, ref.revisionRef)), None)
    except (StudioError, ProjectRepositoryError, OSError, ValueError, KeyError, TypeError):
        return None
    if document is None or ref.pageIndex not in {page.page_index for page in document.pages}:
        return None
    return document


# What an image discussion can hand to the existing Render: its registered
# PNG and JPEG pages (studio.render resolves no other kind).
_RENDER_IMAGE_MIMES = frozenset({"image/png", "image/jpeg"})


def _render_images(project_id: str, project_dir: str, context: ChatRenderContext) -> list[ChatDocument]:
    """Bind a message's selected images to its conversation's project (#253).

    Checked before anything is kept or started: each page is a registration of
    this project at exactly the run, digest, revision (null included) and page
    it names; it is a PNG or JPEG image, as Render reads; and no registered
    page has replaced it since it was chosen. A page that fails is refused for
    what it is. Nothing picks a newer, similarly named or nearby image instead.
    """

    from archflow.project.repository import ProjectRepositoryError
    from project_runtime.application.artifacts import list_documents
    from project_runtime.errors import StudioError

    binding = _project_binding(project_id, project_dir)
    bound = []
    for position, (role, ref) in enumerate((("source", context.source),
                                            *(("reference", ref) for ref in context.references))):
        what = "The source image" if role == "source" else f"Reference {position}"
        document = _registered_page(binding, ref)
        if document is None:
            raise HubFailure(409, "CHAT_RENDER_IMAGE_UNAVAILABLE",
                             f"{what} is not a registered page of this project at that exact revision and page. "
                             "Select it again on the Board; nothing was sent.")
        if document.mime_type not in _RENDER_IMAGE_MIMES:
            raise HubFailure(422, "CHAT_RENDER_IMAGE_UNSUPPORTED",
                             f"{what}, «{document.file_name}», is {document.mime_type}; an image discussion takes a "
                             "registered PNG or JPEG image, as Render does. Nothing was sent.")
        bound.append((role, ref, document, what))
    try:
        replaced = {(page.run_id, page.asset_sha256, page.revision_ref, page.page_index)
                    for row in list_documents(binding) for page in row.replaces_pages}
    except (StudioError, ProjectRepositoryError, OSError, ValueError, KeyError, TypeError) as exc:
        raise HubFailure(503, "CHAT_RENDER_IMAGE_UNREADABLE", "The project's documents could not be read to check the "
                         "selected images, so nothing was sent.") from exc
    for role, ref, document, what in bound:
        if (ref.runId, ref.assetSha256, ref.revisionRef, ref.pageIndex) in replaced:
            raise HubFailure(409, "CHAT_RENDER_IMAGE_STALE",
                             f"{what}, «{document.file_name}» page {ref.pageIndex + 1}, has a newer registered "
                             "replacement. Select the current page on the Board; nothing was sent.")
    return [ChatDocument(**ref.model_dump(), fileName=document.file_name, mimeType=document.mime_type, role=role)
            for role, ref, document, _ in bound]
