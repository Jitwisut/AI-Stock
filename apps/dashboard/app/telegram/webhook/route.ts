import { NextRequest, NextResponse } from "next/server";

export const dynamic = "force-dynamic";

function getBackendUrl(request: NextRequest): URL {
  const baseUrl = process.env.BACKEND_API_BASE_URL ?? process.env.NEXT_PUBLIC_API_BASE_URL;
  if (!baseUrl) {
    throw new Error("Set BACKEND_API_BASE_URL or NEXT_PUBLIC_API_BASE_URL to the FastAPI backend URL.");
  }

  const backendUrl = new URL("/telegram/webhook", baseUrl);
  if (backendUrl.host === request.nextUrl.host) {
    throw new Error("BACKEND_API_BASE_URL must point to the FastAPI backend, not this dashboard URL.");
  }
  return backendUrl;
}

export async function POST(request: NextRequest) {
  try {
    const backendUrl = getBackendUrl(request);
    const response = await fetch(backendUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: await request.text(),
      cache: "no-store"
    });
    const body = await response.json().catch(() => ({ ok: false, detail: "Invalid backend response" }));
    return NextResponse.json(body, { status: response.status });
  } catch (error) {
    return NextResponse.json(
      { ok: false, detail: error instanceof Error ? error.message : "Unknown error" },
      { status: 500 }
    );
  }
}
