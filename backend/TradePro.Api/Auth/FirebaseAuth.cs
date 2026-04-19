using Microsoft.AspNetCore.Authentication.JwtBearer;
using Microsoft.IdentityModel.Tokens;

namespace TradePro.Api.Auth;

/// Validates Firebase ID tokens issued by the `smsp-291e3` project and
/// gates the API to a whitelist of user IDs. Firebase signs tokens with
/// rotating RSA keys published at a well-known URL — `JwtBearer` fetches and
/// caches them automatically.
public static class FirebaseAuth
{
    public const string Scheme = JwtBearerDefaults.AuthenticationScheme;

    public static IServiceCollection AddFirebaseAuth(
        this IServiceCollection services, IConfiguration config, IHostEnvironment env)
    {
        var projectId = config["Firebase:ProjectId"];
        // RequireAuth defaults to true in non-development environments and false
        // locally so `dotnet run` works without signing in.
        var requireAuth = config.GetValue<bool?>("Firebase:RequireAuth")
            ?? !env.IsDevelopment();

        if (requireAuth && string.IsNullOrWhiteSpace(projectId))
        {
            throw new InvalidOperationException(
                "Firebase:ProjectId must be set when Firebase:RequireAuth is true.");
        }

        services.AddAuthentication(Scheme)
            .AddJwtBearer(options =>
            {
                if (!requireAuth || string.IsNullOrWhiteSpace(projectId)) return;
                options.Authority = $"https://securetoken.google.com/{projectId}";
                options.TokenValidationParameters = new TokenValidationParameters
                {
                    ValidateIssuer = true,
                    ValidIssuer = $"https://securetoken.google.com/{projectId}",
                    ValidateAudience = true,
                    ValidAudience = projectId,
                    ValidateLifetime = true,
                    ClockSkew = TimeSpan.FromMinutes(1),
                };
            });

        services.AddAuthorization(options =>
        {
            var allowed = config.GetSection("Firebase:AllowedUserIds").Get<string[]>() ?? [];
            options.AddPolicy("AllowedUsers", policy =>
            {
                if (!requireAuth)
                {
                    // Dev mode: anyone can call the API.
                    policy.RequireAssertion(_ => true);
                    return;
                }
                policy.RequireAuthenticatedUser();
                if (allowed.Length > 0)
                {
                    policy.RequireAssertion(ctx =>
                    {
                        var uid = ctx.User.FindFirst("user_id")?.Value
                            ?? ctx.User.FindFirst("sub")?.Value;
                        return uid is not null && allowed.Contains(uid);
                    });
                }
            });
        });

        return services;
    }
}
