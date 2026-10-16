import { NextRequest, NextResponse } from "next/server";

// API_URL is read at runtime (server-only); NEXT_PUBLIC_API_URL is inlined at build.
const API_URL = process.env.API_URL || process.env.NEXT_PUBLIC_API_URL || "http://localhost:8002";

// Long-running streams (chat SSE, OCR ingestion, fact-check) need the
// Amplify SSR Lambda to live well past the 30-second default. 300s is
// safely under Amplify's 15-minute hard ceiling.
export const maxDuration = 300;
export const dynamic = "force-dynamic";

// Next 15: route handler `params` is now a Promise. Await it before use.
type RouteParams = { params: Promise<{ path: string[] }> };

export async function GET(request: NextRequest, { params }: RouteParams) {
  const { path } = await params;
  return proxyRequest(request, path);
}

export async function POST(request: NextRequest, { params }: RouteParams) {
  const { path } = await params;
  return proxyRequest(request, path);
}

export async function PATCH(request: NextRequest, { params }: RouteParams) {
  const { path } = await params;
  return proxyRequest(request, path);
}

export async function DELETE(request: NextRequest, { params }: RouteParams) {
  const { path } = await params;
  return proxyRequest(request, path);
}

export async function PUT(request: NextRequest, { params }: RouteParams) {
  const { path } = await params;
  return proxyRequest(request, path);
}

async function proxyRequest(request: NextRequest, pathSegments: string[]) {
  const path = pathSegments.join("/");
  // Check the raw URL string for trailing slash — Next.js normalizes pathname
  const rawUrl = request.url;
  const queryIdx = rawUrl.indexOf("?");
  const pathPart = queryIdx >= 0 ? rawUrl.slice(0, queryIdx) : rawUrl;
  const trailingSlash = pathPart.endsWith("/") ? "/" : "";
  let url = `${API_URL}/api/${path}${trailingSlash}${request.nextUrl.search}`;

  const headers: Record<string, string> = {};
  request.headers.forEach((value, key) => {
    if (!["host", "connection", "transfer-encoding"].includes(key.toLowerCase())) {
      headers[key] = value;
    }
  });

  try {
    const fetchOptions: RequestInit = {
      method: request.method,
      headers,
      redirect: "manual",
    };

    let bodyForRetry: any = null;
    if (request.method !== "GET" && request.method !== "HEAD") {
      const contentType = request.headers.get("content-type") || "";
      if (contentType.includes("application/json")) {
        const text = await request.text();
        fetchOptions.body = text;
        bodyForRetry = text;
      } else if (contentType.includes("multipart/form-data")) {
        const buf = await request.arrayBuffer();
        fetchOptions.body = buf;
        bodyForRetry = buf;
        delete (headers as any)["content-type"];
      }
    }

    let response = await fetch(url, fetchOptions);

    // Manually follow same-origin redirects (FastAPI 307 for trailing slash, etc.)
    let redirectCount = 0;
    while (response.status >= 300 && response.status < 400 && response.headers.get("location") && redirectCount < 3) {
      const location = response.headers.get("location")!;
      url = location.startsWith("http") ? location : `${API_URL}${location}`;
      const retryOpts: RequestInit = {
        method: request.method,
        headers,
        redirect: "manual",
      };
      if (bodyForRetry !== null) retryOpts.body = bodyForRetry;
      response = await fetch(url, retryOpts);
      redirectCount++;
    }

    // Stream the response back
    const responseHeaders = new Headers();
    response.headers.forEach((value, key) => {
      if (!["transfer-encoding", "connection"].includes(key.toLowerCase())) {
        responseHeaders.set(key, value);
      }
    });

    // For streaming responses (SSE), pipe through
    if (response.headers.get("content-type")?.includes("text/event-stream")) {
      return new NextResponse(response.body, {
        status: response.status,
        headers: {
          "Content-Type": "text/event-stream",
          "Cache-Control": "no-cache",
          "Connection": "keep-alive",
        },
      });
    }

    // For binary responses (PDFs, images)
    if (response.headers.get("content-type")?.includes("application/pdf") ||
        response.headers.get("content-type")?.includes("image/")) {
      const buffer = await response.arrayBuffer();
      return new NextResponse(buffer, {
        status: response.status,
        headers: responseHeaders,
      });
    }

    // For JSON/text responses — buffer (Amplify SSR Lambda doesn't reliably stream)
    const body = await response.text();
    return new NextResponse(body, {
      status: response.status,
      headers: responseHeaders,
    });

  } catch (error: any) {
    return NextResponse.json(
      { error: "API proxy error", detail: error.message },
      { status: 502 }
    );
  }
}
