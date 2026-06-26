import { NextRequest, NextResponse } from "next/server";

export const dynamic = "force-dynamic";

function getBackendUrl(request: NextRequest): URL {
  const baseUrl = process.env.BACKEND_API_BASE_URL ?? process.env.NEXT_PUBLIC_API_BASE_URL;
  if (!baseUrl) {
    throw new Error("Set BACKEND_API_BASE_URL or NEXT_PUBLIC_API_BASE_URL to the FastAPI backend URL.");
  }

  const backendUrl = new URL("/telegram/set-webhook", baseUrl);
  if (backendUrl.host === request.nextUrl.host) {
    throw new Error("BACKEND_API_BASE_URL must point to the FastAPI backend, not this dashboard URL.");
  }

  const webhookUrl = request.nextUrl.searchParams.get("url");
  if (webhookUrl) {
    backendUrl.searchParams.set("url", webhookUrl);
  }
  return backendUrl;
}

export async function GET(request: NextRequest) {
  try {
    const backendUrl = getBackendUrl(request);
    const response = await fetch(backendUrl, { cache: "no-store" });
    const body = await response.json().catch(() => ({ status: "error", detail: "Invalid backend response" }));
    return NextResponse.json(body, { status: response.status });
  } catch (error) {
    return NextResponse.json(
      { status: "error", detail: error instanceof Error ? error.message : "Unknown error" },
      { status: 500 }
    );
  }
}

export async function POST(request: NextRequest) {
  return GET(request);
}
