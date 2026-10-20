import AuthenticationServices
import CryptoKit
import Foundation
import Security
import UIKit

enum VehicleConnectionError: Error { case unavailable, invalidCallback, secureStorage }

/// Only a Volta session cookie is stored on the phone. OEM tokens stay on the server.
private enum VehicleSessionKeychain {
    private static let service = "tech.arsik.volta.vehicle-session.v1"

    static func read(origin: String) -> String? {
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service, kSecAttrAccount as String: origin,
            kSecReturnData as String: true, kSecMatchLimit as String: kSecMatchLimitOne]
        var result: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess,
              let data = result as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    static func save(_ value: String?, origin: String) throws {
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service, kSecAttrAccount as String: origin]
        guard let value else {
            let deletion = SecItemDelete(query as CFDictionary)
            guard deletion == errSecSuccess || deletion == errSecItemNotFound else { throw VehicleConnectionError.secureStorage }
            return
        }
        let attributes: [String: Any] = [kSecValueData as String: Data(value.utf8),
            kSecAttrAccessible as String: kSecAttrAccessibleWhenUnlockedThisDeviceOnly]
        let update = SecItemUpdate(query as CFDictionary, attributes as CFDictionary)
        if update == errSecItemNotFound {
            var item = query
            attributes.forEach { item[$0.key] = $0.value }
            guard SecItemAdd(item as CFDictionary, nil) == errSecSuccess else { throw VehicleConnectionError.secureStorage }
        } else if update != errSecSuccess { throw VehicleConnectionError.secureStorage }
    }
}

/// No shared URL cache, browser cookie jar, redirect following, or credential in a URL.
@MainActor
final class VehicleConnectionService: NSObject, URLSessionTaskDelegate {
    static let cookieName = "volta_vehicle_session"
    let baseURL: URL
    let origin: String
    private var cookie: String?
    private lazy var session: URLSession = {
        let config = URLSessionConfiguration.ephemeral
        config.urlCache = nil
        config.httpCookieStorage = nil
        config.httpShouldSetCookies = false
        config.requestCachePolicy = .reloadIgnoringLocalCacheData
        config.timeoutIntervalForRequest = 25
        config.timeoutIntervalForResource = 30
        return URLSession(configuration: config, delegate: self, delegateQueue: nil)
    }()

    init(baseURL: URL) {
        self.baseURL = baseURL
        var components = URLComponents(url: baseURL, resolvingAgainstBaseURL: false)!
        components.path = ""
        components.query = nil
        components.fragment = nil
        origin = components.string!
        cookie = VehicleSessionKeychain.read(origin: origin)
        super.init()
    }

    func invalidate() { session.invalidateAndCancel() }

    func request(_ path: String = "", body: [String: String]? = nil) async throws -> VehicleConnection {
        let url = baseURL.appendingPathComponent("api/vehicles" + path)
        var request = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData)
        request.httpShouldHandleCookies = false
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue("no-store", forHTTPHeaderField: "Cache-Control")
        request.setValue(origin, forHTTPHeaderField: "Origin")
        if let cookie { request.setValue(Self.cookieName + "=" + cookie, forHTTPHeaderField: "Cookie") }
        if let body {
            request.httpMethod = "POST"
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = try JSONSerialization.data(withJSONObject: body)
        }
        let (data, response) = try await session.data(for: request)
        if let http = response as? HTTPURLResponse, http.url == url, http.statusCode == 401 {
            try VehicleSessionKeychain.save(nil, origin: origin)
            cookie = nil
        }
        guard let http = response as? HTTPURLResponse, http.url == url,
              (200..<300).contains(http.statusCode), data.count < 2_000_000 else {
            throw VehicleConnectionError.unavailable
        }
        if let raw = http.value(forHTTPHeaderField: "Set-Cookie"),
           let received = HTTPCookie.cookies(withResponseHeaderFields: ["Set-Cookie": raw], for: url)
            .first(where: { $0.name == Self.cookieName }) {
            let next = received.value.isEmpty || (received.expiresDate.map { $0 <= Date() } ?? false)
                ? nil : received.value
            try VehicleSessionKeychain.save(next, origin: origin)
            cookie = next
        }
        let decoded = try JSONDecoder().decode(VehicleConnection.self, from: data)
        if !decoded.connected && decoded.connectionStatus != "reconnect" {
            try VehicleSessionKeychain.save(nil, origin: origin)
            cookie = nil
        }
        return decoded
    }

    nonisolated func urlSession(_ session: URLSession, task: URLSessionTask,
                    willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) {
        // A malicious or misconfigured redirect must never forward the native cookie.
        completionHandler(nil)
    }
}

@MainActor
final class VehicleAuthorization: NSObject, ASWebAuthenticationPresentationContextProviding {
    private var session: ASWebAuthenticationSession?

    func authorize(service: VehicleConnectionService) async throws -> (code: String, verifier: String) {
        var random = [UInt8](repeating: 0, count: 32)
        guard SecRandomCopyBytes(kSecRandomDefault, random.count, &random) == errSecSuccess else {
            throw VehicleConnectionError.unavailable
        }
        let verifier = Self.base64URL(Data(random))
        let challenge = Self.base64URL(Data(SHA256.hash(data: Data(verifier.utf8))))
        var components = URLComponents(url: service.baseURL.appendingPathComponent("api/vehicles/native/start"),
                                       resolvingAgainstBaseURL: false)!
        components.queryItems = [URLQueryItem(name: "challenge", value: challenge)]
        guard let url = components.url else { throw VehicleConnectionError.unavailable }
        let callback: URL = try await withCheckedThrowingContinuation { continuation in
            let auth = ASWebAuthenticationSession(url: url, callbackURLScheme: "voltagovehicle") { url, error in
                if let error { continuation.resume(throwing: error) }
                else if let url { continuation.resume(returning: url) }
                else { continuation.resume(throwing: VehicleConnectionError.invalidCallback) }
            }
            auth.presentationContextProvider = self
            auth.prefersEphemeralWebBrowserSession = true
            session = auth
            if !auth.start() {
                session = nil
                continuation.resume(throwing: VehicleConnectionError.unavailable)
            }
        }
        session = nil
        return (try Self.handoffCode(from: callback), verifier)
    }

    nonisolated static func handoffCode(from callback: URL) throws -> String {
        guard callback.scheme == "voltagovehicle", callback.host == "connection", callback.path == "/callback",
              callback.user == nil, callback.password == nil, callback.fragment == nil,
              let items = URLComponents(url: callback, resolvingAgainstBaseURL: false)?.queryItems,
              items.count == 1, items.first?.name == "code", let code = items.first?.value,
              (32...128).contains(code.count), code.allSatisfy({ $0.isASCII && ($0.isLetter || $0.isNumber || $0 == "-" || $0 == "_") })
        else { throw VehicleConnectionError.invalidCallback }
        return code
    }

    func cancel() { session?.cancel(); session = nil }

    func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor {
        UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }
            .first(where: { $0.activationState == .foregroundActive })?.windows.first(where: \.isKeyWindow)
            ?? ASPresentationAnchor()
    }

    private static func base64URL(_ data: Data) -> String {
        data.base64EncodedString().replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_").replacingOccurrences(of: "=", with: "")
    }
}
