import SwiftUI
import UniformTypeIdentifiers

struct ContentView: View {
    @AppStorage("serverURL") private var serverURL = Defaults.serverURL
    @AppStorage("token") private var token = Defaults.token

    @State private var months: [String] = []
    @State private var tab: String?
    @State private var status: MonthStatus?
    @State private var runState: RunStatus?
    @State private var errorText: String?
    @State private var showSettings = false
    @State private var showImporter = false
    @State private var showRemindConfirm = false
    @State private var busy = false
    @State private var notice: String?

    private var api: APIClient { APIClient(baseURL: serverURL, token: token) }
    private func money(_ v: Double) -> String { String(format: "$%.2f", v) }

    var body: some View {
        NavigationStack {
            List {
                if let e = errorText {
                    Section {
                        Text(e).foregroundStyle(.red)
                        Button("Open settings") { showSettings = true }
                    }
                }
                if let n = notice {
                    Section { Text(n).foregroundStyle(.secondary) }
                }
                if let r = runState, r.running {
                    Section("Pipeline") {
                        HStack { ProgressView(); Text("Running for \(r.tab ?? "…")").padding(.leading, 8) }
                    }
                }
                if let s = status {
                    Section(s.tab) {
                        row("Bill total", money(s.billTotal), .primary)
                        row("Paid", "\(s.paidCount) · \(money(s.paidSum))", .green)
                        row("Pending", "\(s.pendingCount) · \(money(s.pendingSum))",
                            s.pendingCount == 0 ? .green : .orange)
                        if !s.announced {
                            Text("Not yet posted to the group").font(.footnote).foregroundStyle(.secondary)
                        }
                    }
                    if !s.owedByPayer.isEmpty {
                        Section("Still owed, by payer") {
                            ForEach(s.owedByPayer) { o in row(o.payer, money(o.amount), .orange) }
                        }
                    }
                    Section("Pending") {
                        let pending = s.people.filter { !$0.isPaid }
                        if pending.isEmpty {
                            Text("Everyone has paid").foregroundStyle(.green)
                        }
                        ForEach(pending) { p in PersonRow(person: p, money: money) }
                    }
                    Section("Paid") {
                        ForEach(s.people.filter { $0.isPaid }) { p in PersonRow(person: p, money: money) }
                    }
                    Section {
                        Text("Updated \(s.fetchedAt.replacingOccurrences(of: "T", with: " "))")
                            .font(.footnote).foregroundStyle(.secondary)
                    }
                } else if errorText == nil {
                    Section { ProgressView("Loading…") }
                }
            }
            .navigationTitle("T-Mobile Bills")
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Menu {
                        ForEach(months, id: \.self) { m in
                            Button(m) { tab = m; Task { await loadStatus() } }
                        }
                    } label: {
                        Label(tab ?? "Month", systemImage: "calendar")
                    }
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button { showSettings = true } label: { Image(systemName: "gear") }
                }
            }
            .safeAreaInset(edge: .bottom) {
                HStack(spacing: 12) {
                    Button { showImporter = true } label: {
                        Label("Add bill (PDF)", systemImage: "doc.badge.plus").frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(busy)
                    Button { showRemindConfirm = true } label: {
                        Label("Remind unpaid", systemImage: "bell").frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.bordered)
                    .disabled(busy || (status?.pendingCount ?? 0) == 0)
                }
                .padding()
                .background(.bar)
            }
            .refreshable { await load() }
            .task {
                while !Task.isCancelled {
                    await load()
                    try? await Task.sleep(for: .seconds(60))
                }
            }
            .sheet(isPresented: $showSettings, onDismiss: { Task { await load() } }) {
                SettingsView()
            }
            .fileImporter(isPresented: $showImporter, allowedContentTypes: [.pdf]) { result in
                Task { await handleImport(result) }
            }
            .confirmationDialog("Send a reminder to everyone still unpaid for \(tab ?? "this month")?",
                                isPresented: $showRemindConfirm, titleVisibility: .visible) {
                Button("Send reminders", role: .destructive) { Task { await remind() } }
            }
        }
    }

    private func row(_ label: String, _ value: String, _ color: Color) -> some View {
        HStack { Text(label); Spacer(); Text(value).bold().foregroundStyle(color) }
    }

    @MainActor private func load() async {
        do {
            let m = try await api.months()
            months = m.months
            runState = m.run
            if let t = tab, months.contains(t) { } else { tab = months.first }
            await loadStatus()
        } catch {
            errorText = error.localizedDescription
        }
    }

    @MainActor private func loadStatus() async {
        do {
            status = try await api.status(tab: tab)
            errorText = nil
        } catch {
            errorText = error.localizedDescription
        }
    }

    @MainActor private func handleImport(_ result: Result<URL, Error>) async {
        guard case .success(let url) = result else { return }
        busy = true
        defer { busy = false }
        do {
            let scoped = url.startAccessingSecurityScopedResource()
            defer { if scoped { url.stopAccessingSecurityScopedResource() } }
            let data = try Data(contentsOf: url)
            notice = "Uploading \(url.lastPathComponent)…"
            let r = try await api.uploadBill(data)
            if let t = r.tab { tab = t }
            notice = "Saved \(r.saved ?? "bill"). Pipeline is building \(r.tab ?? "the month") and posting to the group…"
            await waitForPipeline()
            notice = "Done: \(r.tab ?? "month") is built and posted."
            await load()
        } catch {
            notice = nil
            errorText = error.localizedDescription
        }
    }

    @MainActor private func remind() async {
        guard let t = tab else { return }
        busy = true
        defer { busy = false }
        do {
            _ = try await api.remind(tab: t)
            notice = "Sending reminders for \(t)…"
            await waitForPipeline()
            notice = "Reminders sent for \(t)."
            await load()
        } catch {
            errorText = error.localizedDescription
        }
    }

    @MainActor private func waitForPipeline() async {
        for _ in 0..<120 {   // up to 4 minutes
            try? await Task.sleep(for: .seconds(2))
            if let r = try? await api.run() {
                runState = r
                if !r.running { return }
            }
        }
    }
}

struct PersonRow: View {
    let person: Person
    let money: (Double) -> String

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack {
                Text(person.name)
                Spacer()
                Text(money(person.total)).foregroundStyle(person.isPaid ? .green : .primary)
            }
            if person.payer != person.name {
                Text("Covered by \(person.payer)").font(.caption).foregroundStyle(.secondary)
            }
            if !person.note.isEmpty {
                Text(person.note).font(.caption2).foregroundStyle(.secondary)
            }
        }
    }
}
