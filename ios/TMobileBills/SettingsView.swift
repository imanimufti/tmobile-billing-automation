import SwiftUI

struct SettingsView: View {
    @Environment(\.dismiss) private var dismiss
    @AppStorage("serverURL") private var serverURL = Defaults.serverURL
    @AppStorage("token") private var token = Defaults.token
    @State private var result: String?

    var body: some View {
        NavigationStack {
            Form {
                Section("Mac server") {
                    TextField("http://imanis-macbook-pro.local:8787", text: $serverURL)
                        .keyboardType(.URL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                    SecureField("Token", text: $token)
                    Button("Test connection") {
                        Task {
                            do {
                                let host = try await APIClient(baseURL: serverURL, token: token).ping()
                                result = "Connected to \(host)"
                            } catch {
                                result = error.localizedDescription
                            }
                        }
                    }
                    if let r = result { Text(r).font(.footnote) }
                }
                Section {
                    Text("The Mac runs the pipeline, WhatsApp and the sheet. This app only talks to it on your network.")
                        .font(.footnote).foregroundStyle(.secondary)
                }
            }
            .navigationTitle("Settings")
            .toolbar {
                ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } }
            }
        }
    }
}
