import Foundation

/// Release builds accept one bundled public HTTPS service root. Missing or unsafe values
/// resolve to a reserved non-endpoint, so Release never falls back to a private host,
/// cleartext transport, or user-controlled preference.
enum ReleaseEndpointPolicy {
    static let unconfiguredURL = URL(string: "https://configuration.invalid")!

    private enum PathPolicy {
        case serviceRoot
        case hostedDocument
    }

    static func resolvedURL(from rawValue: String?) -> URL {
        validatedURL(from: rawValue, pathPolicy: .serviceRoot) ?? unconfiguredURL
    }

    static func resolvedDocumentURL(from rawValue: String?) -> URL? {
        validatedURL(from: rawValue, pathPolicy: .hostedDocument)
    }

    static func isConfigured(_ url: URL) -> Bool {
        url != unconfiguredURL && resolvedURL(from: url.absoluteString) == url
    }

    private static func validatedURL(
        from rawValue: String?,
        pathPolicy: PathPolicy
    ) -> URL? {
        guard let rawValue else { return nil }
        let candidate = rawValue.trimmingCharacters(in: .whitespacesAndNewlines)
        guard
            !candidate.isEmpty,
            candidate.unicodeScalars.allSatisfy(\.isASCII),
            !candidate.unicodeScalars.contains(where: {
                CharacterSet.whitespacesAndNewlines.contains($0)
            }),
            !candidate.contains("%"),
            !candidate.contains("\\"),
            var components = URLComponents(string: candidate),
            components.scheme?.lowercased() == "https",
            let host = components.host?.lowercased(),
            isPublicHostname(host),
            components.user == nil,
            components.password == nil,
            components.query == nil,
            components.fragment == nil,
            hasValidExplicitPortSyntax(candidate, components: components)
        else {
            return nil
        }

        if let port = components.port, !(1...65_535).contains(port) {
            return nil
        }

        switch pathPolicy {
        case .serviceRoot:
            guard
                components.percentEncodedPath.isEmpty
                    || components.percentEncodedPath == "/"
            else {
                return nil
            }
            components.path = ""
        case .hostedDocument:
            let segments = components.path.split(
                separator: "/",
                omittingEmptySubsequences: false
            )
            guard
                !components.path.contains("//"),
                !segments.contains("."),
                !segments.contains("..")
            else {
                return nil
            }
        }

        components.scheme = "https"
        components.host = host
        return components.url
    }

    private static func isPublicHostname(_ host: String) -> Bool {
        let reservedSuffixes = [
            "localhost",
            "local",
            "internal",
            "invalid",
            "test",
            "example",
            "home.arpa",
        ]
        guard
            host.count <= 253,
            host.contains("."),
            !host.contains(":"),
            !reservedSuffixes.contains(where: {
                host == $0 || host.hasSuffix(".\($0)")
            })
        else {
            return false
        }

        let labels = host.split(separator: ".", omittingEmptySubsequences: false)
        guard
            !labels.isEmpty,
            !labels.allSatisfy(isDecimalLabel),
            let topLevelDomain = labels.last,
            topLevelDomain.count >= 2,
            topLevelDomain.unicodeScalars.allSatisfy(isASCIIAlpha)
        else {
            return false
        }

        return labels.allSatisfy { label in
            let scalars = label.unicodeScalars
            guard
                !label.isEmpty,
                label.count <= 63,
                !label.hasPrefix("xn--"),
                !(label.hasPrefix("0x")
                    && label.dropFirst(2).unicodeScalars.allSatisfy(isASCIIHexDigit)),
                let first = scalars.first,
                let last = scalars.last,
                isASCIIAlphaNumeric(first),
                isASCIIAlphaNumeric(last)
            else {
                return false
            }
            return scalars.allSatisfy {
                isASCIIAlphaNumeric($0) || $0.value == 45 // "-"
            }
        }
    }

    private static func hasValidExplicitPortSyntax(
        _ candidate: String,
        components: URLComponents
    ) -> Bool {
        guard let schemeSeparator = candidate.range(of: "://") else { return false }
        let remainder = candidate[schemeSeparator.upperBound...]
        let authority = remainder.prefix { character in
            character != "/" && character != "?" && character != "#"
        }
        let colonCount = authority.filter { $0 == ":" }.count
        return colonCount == 0 || (colonCount == 1 && components.port != nil)
    }

    private static func isDecimalLabel(_ label: Substring) -> Bool {
        !label.isEmpty && label.unicodeScalars.allSatisfy {
            (48...57).contains($0.value)
        }
    }

    private static func isASCIIAlpha(_ scalar: Unicode.Scalar) -> Bool {
        (65...90).contains(scalar.value) || (97...122).contains(scalar.value)
    }

    private static func isASCIIAlphaNumeric(_ scalar: Unicode.Scalar) -> Bool {
        isASCIIAlpha(scalar) || (48...57).contains(scalar.value)
    }

    private static func isASCIIHexDigit(_ scalar: Unicode.Scalar) -> Bool {
        (48...57).contains(scalar.value)
            || (65...70).contains(scalar.value)
            || (97...102).contains(scalar.value)
    }
}

enum VoltaRuntimeConfiguration {
    #if DEBUG
    #if targetEnvironment(simulator)
    static let debugDefaultBaseURLString = "http://127.0.0.1:8773"
    #else
    // Device builds: your Mac's Bonjour name. Settings can change it at runtime.
    static let debugDefaultBaseURLString = "http://your-mac.local:8773"
    #endif

    static func debugURL(from rawValue: String) -> URL {
        guard
            let url = URL(string: rawValue.trimmingCharacters(in: .whitespacesAndNewlines)),
            let scheme = url.scheme?.lowercased(),
            scheme == "http" || scheme == "https",
            url.host != nil
        else {
            return URL(string: debugDefaultBaseURLString)!
        }
        return url
    }

    /// One-time compatibility bridge for the old Mac LAN address that shipped as the
    /// Simulator default before loopback became canonical. Keep every explicit custom
    /// endpoint intact; only the exact retired default is migrated.
    static func migratedDebugBaseURLString(_ rawValue: String) -> String {
        #if targetEnvironment(simulator)
        let candidate = rawValue.trimmingCharacters(in: .whitespacesAndNewlines)
        if candidate == "http://192.168.1.220:8773"
            || candidate == "http://192.168.1.220:8773/" {
            return debugDefaultBaseURLString
        }
        #endif
        return rawValue
    }
    #else
    static var baseURL: URL {
        let bundled = Bundle.main.object(forInfoDictionaryKey: "VoltaReleaseBaseURL") as? String
        return ReleaseEndpointPolicy.resolvedURL(from: bundled)
    }

    static var privacyPolicyURL: URL? {
        hostedDocumentURL(forInfoDictionaryKey: "VoltaPrivacyPolicyURL")
    }

    static var termsOfUseURL: URL? {
        hostedDocumentURL(forInfoDictionaryKey: "VoltaTermsURL")
    }

    static var supportURL: URL? {
        hostedDocumentURL(forInfoDictionaryKey: "VoltaSupportURL")
    }

    private static func hostedDocumentURL(forInfoDictionaryKey key: String) -> URL? {
        let bundled = Bundle.main.object(forInfoDictionaryKey: key) as? String
        return ReleaseEndpointPolicy.resolvedDocumentURL(from: bundled)
    }
    #endif
}
