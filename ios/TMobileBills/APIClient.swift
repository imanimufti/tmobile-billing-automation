import Foundation

enum APIError: LocalizedError {
    case badURL
    case http(Int, String)
    case decoding(String)

    var errorDescription: String? {
        switch self {
        case .badURL: return "The server address is not a valid URL."
        case .http(let code, let msg): return "Server said \(code): \(msg)"
        case .decoding(let msg): return "Unexpected response: \(msg)"
        }
    }
}

final class APIClient {
    let baseURL: String
    let token: String

    init(baseURL: String, token: String) {
        self.baseURL = baseURL
        self.token = token
    }

    private let decoder: JSONDecoder = {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        return d
    }()

    private func request(_ path: String, method: String = "GET",
                         query: [String: String] = [:],
                         body: Data? = nil, contentType: String? = nil) throws -> URLRequest {
        guard var comps = URLComponents(string: baseURL.trimmingCharacters(in: .whitespacesAndNewlines)) else {
            throw APIError.badURL
        }
        comps.path = path
        if !query.isEmpty {
            comps.queryItems = query.map { URLQueryItem(name: $0.key, value: $0.value) }
        }
        guard let url = comps.url else { throw APIError.badURL }
        var req = URLRequest(url: url, timeoutInterval: 60)
        req.httpMethod = method
        req.setValue(token, forHTTPHeaderField: "X-Token")
        if let ct = contentType { req.setValue(ct, forHTTPHeaderField: "Content-Type") }
        req.httpBody = body
        return req
    }

    private func send<T: Decodable>(_ req: URLRequest, as type: T.Type) async throws -> T {
        let (data, resp) = try await URLSession.shared.data(for: req)
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        guard (200..<300).contains(code) else {
            let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
            let msg = (obj?["error"] as? String) ?? (String(data: data, encoding: .utf8) ?? "")
            throw APIError.http(code, msg)
        }
        do { return try decoder.decode(T.self, from: data) }
        catch { throw APIError.decoding(error.localizedDescription) }
    }

    private struct Ping: Decodable { let ok: Bool; let host: String }

    func ping() async throws -> String {
        try await send(request("/api/ping"), as: Ping.self).host
    }

    func months() async throws -> MonthsResponse {
        try await send(request("/api/months"), as: MonthsResponse.self)
    }

    func status(tab: String?) async throws -> MonthStatus {
        let q: [String: String] = tab.map { ["tab": $0] } ?? [:]
        return try await send(request("/api/status", query: q), as: MonthStatus.self)
    }

    func run() async throws -> RunStatus {
        try await send(request("/api/run"), as: RunStatus.self)
    }

    func uploadBill(_ pdf: Data) async throws -> ActionResponse {
        try await send(request("/api/bill", method: "POST", body: pdf,
                               contentType: "application/pdf"), as: ActionResponse.self)
    }

    func remind(tab: String) async throws -> ActionResponse {
        try await send(request("/api/remind", method: "POST", query: ["tab": tab]),
                       as: ActionResponse.self)
    }

    func refresh() async throws -> ActionResponse {
        try await send(request("/api/refresh", method: "POST"), as: ActionResponse.self)
    }
}
