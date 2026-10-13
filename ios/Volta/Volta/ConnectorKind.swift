import Foundation

/// Plug families are physical standards, not interchangeable regional names.
enum ConnectorKind {
    static let choices = ["CCS2", "CCS1", "Type 2", "Type 1", "CHAdeMO", "NACS", "GB/T AC", "GB/T DC", "Tesla", "Schuko"]

    static func canonical(_ label: String) -> String {
        let token = label.lowercased().filter { $0.isLetter || $0.isNumber }
        switch token {
        case "ccs1", "ccstype1", "combotype1", "combo1", "saej1772combo": return "ccs1"
        case "ccs2", "ccstype2", "combotype2", "combo2": return "ccs2"
        case "type1", "j1772", "saej1772", "type1j1772": return "type1"
        case "type2", "mennekes", "iec621962": return "type2"
        case "nacs", "saej3400", "j3400": return "nacs"
        default: return token
        }
    }

    static func matches(_ label: String, selection: String) -> Bool {
        canonical(label) == canonical(selection)
    }
}
