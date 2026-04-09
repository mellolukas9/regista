import type { NextAuthOptions } from "next-auth";
import CredentialsProvider from "next-auth/providers/credentials";

export const authOptions: NextAuthOptions = {
  providers: [
    CredentialsProvider({
      name: "credentials",
      credentials: {
        email: { label: "Email", type: "email" },
        password: { label: "Senha", type: "password" },
      },
      async authorize(credentials) {
        if (!credentials?.email || !credentials?.password) return null;

        const form = new URLSearchParams();
        form.append("username", credentials.email);
        form.append("password", credentials.password);

        const res = await fetch(
          `${process.env.NEXT_PUBLIC_API_URL}/api/auth/login`,
          { method: "POST", body: form }
        );

        if (!res.ok) return null;

        const data = await res.json();

        // Decode JWT payload para extrair client_id e role
        const payload = JSON.parse(
          Buffer.from(data.access_token.split(".")[1], "base64").toString()
        );

        return {
          id: payload.user_id,
          email: credentials.email,
          accessToken: data.access_token,
          clientId: payload.client_id,
          role: payload.role,
        };
      },
    }),
  ],
  callbacks: {
    async jwt({ token, user }) {
      if (user) {
        token.accessToken = (user as any).accessToken;
        token.clientId = (user as any).clientId;
        token.role = (user as any).role;
      }
      return token;
    },
    async session({ session, token }) {
      (session as any).accessToken = token.accessToken;
      (session as any).clientId = token.clientId;
      (session as any).role = token.role;
      return session;
    },
  },
  pages: {
    signIn: "/login",
  },
  session: { strategy: "jwt" },
};
