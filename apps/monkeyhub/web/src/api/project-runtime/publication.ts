import type { ServerConnection } from "./connection";
import { call, type FieldsResult } from "./error";
import { readPublicationApiPublicationGet, savePublicationApiPublicationPut,
  publicationFromBoardApiPublicationFromBoardPost, exportApiPublicationExportPost } from "./generated";
import type { PublicationRequestDto, PublicationBoardRequestDto, PublicationExportRequestDto } from "./generated";

export const publicationClient = (connection: ServerConnection) => ({
  read: () => call("GET /api/publication", readPublicationApiPublicationGet({ client: connection.client })),
  save: (body: PublicationRequestDto) => call("PUT /api/publication", savePublicationApiPublicationPut({ client: connection.client, body })),
  fromBoard: (body: PublicationBoardRequestDto) => call("POST /api/publication/from-board", publicationFromBoardApiPublicationFromBoardPost({ client: connection.client, body })),
  export: (body: PublicationExportRequestDto) => call("POST /api/publication/export", exportApiPublicationExportPost({ client: connection.client, body, parseAs: "blob" }) as Promise<FieldsResult<Blob>>),
});
