export interface RoomCreated {
  roomCode: string;
  name: string | null;
  expiresAt: string;
  accessToken: string;
  shareUrl: string;
  ownerUrl: string;
  passwordRequired: boolean;
  guestUploadEnabled: boolean;
  guestDownloadEnabled: boolean;
}

export interface RoomView {
  roomCode: string;
  name: string | null;
  status: string;
  guestUploadEnabled: boolean;
  guestDownloadEnabled: boolean;
  userCount: number;
  maxUsers: number;
  storageUsedBytes: number;
  fileCount: number;
  createdAt: string;
  expiresAt: string;
  passwordRequired?: boolean;
  remainingSeconds?: number;
  canJoin?: boolean;
}

export interface JoinResponse {
  accessToken: string;
  room: RoomView;
  role: string;
  displayName: string | null;
  sessionExpiresAt: string;
  wsUrl: string;
}

export interface FileView {
  id: number;
  originalFilename: string;
  contentType: string | null;
  sizeBytes: number;
  status: string;
  downloadCount: number;
  uploadedByName: string | null;
  createdAt: string;
}

export interface MessageView {
  id: number;
  displayName: string | null;
  body: string;
  createdAt: string;
}

export interface RoomMember {
  sessionId: string;
  displayName: string | null;
  role: string;
}

export interface RoomState {
  room: {
    roomCode: string;
    name: string | null;
    guestUploadEnabled: boolean;
    guestDownloadEnabled: boolean;
    userCount: number;
    maxUsers: number;
    storageUsedBytes: number;
    fileCount: number;
    expiresAt: string;
    remainingSeconds: number;
  };
  role: string;
  me: { id: string; displayName: string | null };
  members: RoomMember[];
  files: FileView[];
  messages: MessageView[];
}

export type WsEvent =
  | { type: "ROOM_STATE"; payload: RoomState }
  | { type: "ROOM_EXPIRING"; payload: { remainingSeconds: number } }
  | { type: "ROOM_EXPIRED"; payload: Record<string, never> }
  | { type: "USER_JOINED"; payload: { sessionId: string; displayName: string | null; role: string } }
  | { type: "USER_LEFT"; payload: { sessionId: string; displayName: string | null } }
  | { type: "FILE_UPLOAD_COMPLETED"; payload: { file: FileView } }
  | { type: "FILE_DELETED"; payload: { fileId: number } }
  | { type: "FILE_DOWNLOADED"; payload: { fileId: number; downloadCount: number } }
  | { type: "ROOM_SETTINGS_CHANGED"; payload: { room: RoomView } }
  | { type: "CHAT_MESSAGE_SENT"; payload: { message: MessageView } }
  | { type: "CHAT_MESSAGE_DELETED"; payload: { messageId: number } }
  | { type: "PONG" }
  | { type: "ERROR"; payload: { code: string; detail: string } };