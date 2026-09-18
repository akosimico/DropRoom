import RoomClient from "./client";

export default async function RoomPage({
  params,
  searchParams,
}: {
  params: Promise<{ token: string }>;
  searchParams: Promise<{ [key: string]: string | string[] | undefined }>;
}) {
  const { token } = await params;
  const sp = await searchParams;
  const owner = typeof sp.owner === "string" ? sp.owner : null;
  return <RoomClient token={token} ownerToken={owner} />;
}