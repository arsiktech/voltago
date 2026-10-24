import Foundation
import Network

/// A minimal, hermetic, loopback-only HTTP/1.1 GET server used ONLY by VoltaUITests.
///
/// XCUITest UI-testing bundles run the app under test as a SEPARATE OS process from the test
/// bundle itself (unlike VoltaTests' unit tests, which are injected into the app process via
/// TEST_HOST/BUNDLE_LOADER) -- so an in-test `URLProtocol` stub has no effect on the app's real
/// `URLSession.shared` calls; it would only intercept requests made from the test process. This
/// server instead answers real HTTP requests over the loopback interface, standing in for the
/// Volta Python backend so UI tests never depend on a live server or live network.
/// External dependencies must not make deterministic tests flaky. The app is pointed
/// at it via the EXISTING configurable-URL seam -- `-voltaBaseURL http://127.0.0.1:<port>`
/// launch argument, which Foundation registers into UserDefaults' argument domain and
/// `@AppStorage("voltaBaseURL")` (AppState.swift) reads -- so this required NO production code
/// change.
///
/// Built on Network.framework, a first-party system framework -- consistent with the native-only
/// dependency contract in docs/backend-engine.md (same bar as StoreKit/MapKit/WidgetKit).
///
/// The iOS Simulator shares the host Mac's loopback interface (see ios/README.md: "The
/// Simulator shares the Mac's loopback"), so the XCUITest runner process (this code) and the
/// Volta app process (launched separately by XCUITest) can talk to each other over
/// `127.0.0.1` even though they are different OS processes on the same simulator device.
final class StubServer {
    enum StubServerError: Error { case timedOut, noPort }

    /// A deterministic response step for one stub route. Response arrays below advance once
    /// per matching request and then keep returning their final step, which lets a test model
    /// fresh → failure → recovery without racing mutable state across the app/test processes.
    struct ResponseStep {
        let status: String
        let body: String
        let delay: TimeInterval

        init(status: String, body: String, delay: TimeInterval = 0) {
            self.status = status
            self.body = body
            self.delay = delay
        }

        static func ok(_ body: String) -> ResponseStep {
            ResponseStep(status: "200 OK", body: body)
        }

        static var serviceUnavailable: ResponseStep {
            ResponseStep(status: "503 Service Unavailable", body: #"{"error":"temporarily unavailable"}"#)
        }
    }

    /// JSON served for `GET /api/chargers` when the query string contains `overview=true`
    /// (AppState.loadOverview -- the national leads-only overview call fired at launch).
    var overviewChargersJSON: String = StubFixtures.overviewChargersJSON
    /// JSON served for `GET /api/chargers` on every other call (AppState.fetchLive -- the
    /// viewport bbox/lat/lon live fetch fired by locate/search/pan).
    var liveChargersJSON: String = StubFixtures.liveChargersJSON
    /// JSON served for `GET /api/geocode`.
    var geocodeJSON: String = StubFixtures.geocodeJSON

    /// Optional per-route sequences. Empty preserves the original single-payload behavior.
    /// Configure these before launching the app; the server's serial queue owns their indices.
    private var overviewResponses: [ResponseStep] = []
    private var liveResponses: [ResponseStep] = []
    private var geocodeResponses: [ResponseStep] = []
    private var nearbyOverviewResponse: ResponseStep?

    private(set) var port: UInt16 = 0
    private var listener: NWListener?
    /// Serial -- every connection callback below (accept/receive/respond) runs on this same
    /// queue, so `connections` never needs its own lock.
    private let queue = DispatchQueue(label: "VoltaUITests.StubServer")
    private var connections: [ObjectIdentifier: NWConnection] = [:]
    private var overviewResponseIndex = 0
    private var liveResponseIndex = 0
    private var geocodeResponseIndex = 0
    private var recordedRequests: [String] = []
    private var deliveredResponses: [String] = []

    var baseURLString: String { "http://127.0.0.1:\(port)" }

    /// Install deterministic response sequences on the same serial queue that serves
    /// requests. Tests configure the server after `start()`, so direct property mutation
    /// would otherwise race the first request from the separately launched app process.
    func configureOverviewResponses(_ responses: [ResponseStep]) {
        queue.sync {
            overviewResponses = responses
            overviewResponseIndex = 0
        }
    }

    func configureLiveResponses(_ responses: [ResponseStep]) {
        queue.sync {
            liveResponses = responses
            liveResponseIndex = 0
        }
    }

    /// Keep launch overviews separate so a preloaded fixture cannot prove fast location loading.
    func configureNearbyOverviewResponse(_ response: ResponseStep) {
        queue.sync { nearbyOverviewResponse = response }
    }

    /// Availability-watch routes. Unset keeps `/api/watches/*` at 404, which the app reads
    /// as "watches not offered" -- the default for every other test.
    private var watchConfigResponse: ResponseStep?
    private var watchCreateResponse: ResponseStep?
    private var watchRecordResponse: ResponseStep?

    func configureWatchRoutes(config: String, create: ResponseStep, record: String) {
        queue.sync {
            watchConfigResponse = .ok(config)
            watchCreateResponse = create
            watchRecordResponse = .ok(record)
        }
    }

    func configureGeocodeResponses(_ responses: [ResponseStep]) {
        queue.sync {
            geocodeResponses = responses
            geocodeResponseIndex = 0
        }
    }

    func recordedRequestPaths() -> [String] {
        queue.sync { recordedRequests }
    }

    func deliveredResponsePaths() -> [String] {
        queue.sync { deliveredResponses }
    }

    /// Starts listening on an OS-assigned ephemeral loopback port and blocks (briefly) until
    /// the listener actually reaches `.ready`, so callers never race the app's first request
    /// against a not-yet-bound socket. Safe to call from a test's `setUp`.
    func start(timeout: TimeInterval = 10) throws {
        let params = NWParameters.tcp
        params.allowLocalEndpointReuse = true
        // Loopback-only: this never touches a real network interface, never advertises itself
        // (no Bonjour/broadcast), and is unreachable from anywhere but this Mac -- so it never
        // triggers the macOS Local Network permission prompt, and it is inherently hermetic.
        params.requiredLocalEndpoint = NWEndpoint.hostPort(host: "127.0.0.1", port: .any)

        let listener = try NWListener(using: params)
        self.listener = listener

        let readySemaphore = DispatchSemaphore(value: 0)
        var startError: Error?
        listener.stateUpdateHandler = { state in
            switch state {
            case .ready:
                readySemaphore.signal()
            case .failed(let error):
                startError = error
                readySemaphore.signal()
            default:
                break
            }
        }
        listener.newConnectionHandler = { [weak self] connection in
            self?.accept(connection)
        }
        listener.start(queue: queue)

        guard readySemaphore.wait(timeout: .now() + timeout) == .success else {
            listener.cancel()
            throw StubServerError.timedOut
        }
        if let startError {
            throw startError
        }
        guard let assignedPort = listener.port?.rawValue else {
            throw StubServerError.noPort
        }
        port = assignedPort
    }

    func stop() {
        listener?.stateUpdateHandler = nil
        listener?.newConnectionHandler = nil
        listener?.cancel()
        listener = nil
        queue.sync {
            for (_, connection) in connections { connection.cancel() }
            connections.removeAll()
        }
    }

    // MARK: - Connection handling (all on `queue`)

    private func accept(_ connection: NWConnection) {
        let key = ObjectIdentifier(connection)
        connections[key] = connection
        connection.start(queue: queue)
        receive(connection, buffer: Data(), key: key)
    }

    private func receive(_ connection: NWConnection, buffer: Data, key: ObjectIdentifier) {
        connection.receive(minimumIncompleteLength: 1, maximumLength: 65_536) { [weak self] data, _, isComplete, error in
            guard let self else { return }
            var buffer = buffer
            if let data, !data.isEmpty { buffer.append(data) }
            // Every request this stub answers is a GET with no body, so once the blank line
            // that ends the headers has arrived there is nothing further to read.
            if let headerEnd = buffer.range(of: Data("\r\n\r\n".utf8)) {
                self.respond(to: buffer[..<headerEnd.lowerBound], on: connection, key: key)
                return
            }
            if isComplete || error != nil {
                self.connections.removeValue(forKey: key)
                connection.cancel()
                return
            }
            self.receive(connection, buffer: buffer, key: key)
        }
    }

    private func respond(to headerBytes: Data, on connection: NWConnection, key: ObjectIdentifier) {
        let headerText = String(decoding: headerBytes, as: UTF8.self)
        // Extract just the request line ("GET /path HTTP/1.1") without `String.split(separator:)`
        // on a multi-character literal: Swift 5.7+'s RegexComponent-based split overload makes
        // `.split(separator: "\r\n")` an ambiguous-overload compile error in some contexts, so
        // this walks to the first LF (an unambiguous single-Character `firstIndex(of:)`) and
        // trims a trailing CR instead.
        let firstLineEnd = headerText.firstIndex(of: "\n") ?? headerText.endIndex
        var requestLine = String(headerText[..<firstLineEnd])
        if requestLine.hasSuffix("\r") { requestLine.removeLast() }
        // e.g. "GET /api/chargers?overview=true&include_leads=true HTTP/1.1"
        let requestPath = requestLine
            .split(separator: Character(" "), omittingEmptySubsequences: true)
            .dropFirst()
            .first
            .map(String.init) ?? "/"
        recordedRequests.append(requestPath)
        let response = route(requestPath)
        let bodyData = Data(response.body.utf8)

        var headText = "HTTP/1.1 \(response.status)\r\n"
        headText += "Content-Type: application/json; charset=utf-8\r\n"
        headText += "Content-Length: \(bodyData.count)\r\n"
        headText += "Connection: close\r\n\r\n"

        let responseData = Data(headText.utf8) + bodyData

        // Hold this connection without blocking catalogue responses on the server queue.
        queue.asyncAfter(deadline: .now() + response.delay) { [weak self] in
            guard let self, self.connections[key] != nil else { return }
            connection.send(content: responseData, completion: .contentProcessed { [weak self] error in
                if error == nil { self?.deliveredResponses.append(requestPath) }
                self?.connections.removeValue(forKey: key)
                connection.cancel()
            })
        }
    }

    private func route(_ requestPath: String) -> ResponseStep {
        let pathComponents = requestPath.split(separator: Character("?"), maxSplits: 1)
        let path = pathComponents.first.map(String.init) ?? requestPath
        let query = pathComponents.count > 1 ? String(pathComponents[1]) : ""

        switch path {
        case "/api/health":
            return .ok(#"{"ok": true}"#)
        case "/api/geocode":
            return nextStep(
                from: geocodeResponses,
                index: &geocodeResponseIndex,
                fallback: .ok(geocodeJSON)
            )
        case "/api/chargers":
            let isOverview = query.contains("overview=true")
            if isOverview {
                if let nearbyOverviewResponse,
                   URLComponents(string: "http://localhost" + requestPath)?.queryItems?.contains(where: { $0.name == "lat" }) == true {
                    return nearbyOverviewResponse
                }
                return nextStep(
                    from: overviewResponses,
                    index: &overviewResponseIndex,
                    fallback: .ok(overviewChargersJSON)
                )
            }
            return nextStep(
                from: liveResponses,
                index: &liveResponseIndex,
                fallback: .ok(liveChargersJSON)
            )
        case "/api/watches/config" where watchConfigResponse != nil:
            return watchConfigResponse!
        case "/api/watches" where watchCreateResponse != nil:
            // The only POST this stub answers; its body is not read (the server closes after).
            return watchCreateResponse!
        case _ where path.hasPrefix("/api/watches/") && watchRecordResponse != nil:
            return watchRecordResponse!
        default:
            return ResponseStep(status: "404 Not Found", body: #"{"error": "not found"}"#)
        }
    }

    private func nextStep(
        from steps: [ResponseStep],
        index: inout Int,
        fallback: ResponseStep
    ) -> ResponseStep {
        guard !steps.isEmpty else { return fallback }
        let step = steps[min(index, steps.count - 1)]
        if index < steps.count - 1 { index += 1 }
        return step
    }
}
