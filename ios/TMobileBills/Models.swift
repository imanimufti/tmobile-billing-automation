import Foundation

struct Person: Codable, Identifiable, Hashable {
    var id: String { name }
    let name: String
    let total: Double
    let status: String
    let note: String
    let payer: String
    var isPaid: Bool { status == "paid" }
}

struct Owed: Codable, Identifiable, Hashable {
    var id: String { payer }
    let payer: String
    let amount: Double
}

struct MonthStatus: Codable {
    let tab: String
    let people: [Person]
    let paidCount: Int
    let paidSum: Double
    let pendingCount: Int
    let pendingSum: Double
    let owedByPayer: [Owed]
    let billTotal: Double
    let announced: Bool
    let announcedAt: String?
    let fetchedAt: String
}

struct RunStatus: Codable {
    let running: Bool
    let started: String?
    let tab: String?
    let log: [String]?
}

struct MonthsResponse: Codable {
    let months: [String]
    let run: RunStatus
}

struct ActionResponse: Codable {
    let saved: String?
    let tab: String?
    let pipelineStarted: Bool?
    let error: String?
}

/// Baked at build time from the Mac that runs the server.
enum Defaults {
    static let serverURL = "http://imanis-macbook-pro.local:8787"
    static let token = "u4RBTllhAOaKVpeQiRLF2KQM_09QFBt0"
}
