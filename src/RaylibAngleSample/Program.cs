using Raylib_cs;
using static Raylib_cs.Raylib;

SetConfigFlags(ConfigFlags.ResizableWindow | ConfigFlags.VSyncHint);
InitWindow(900, 520, "raylib-cs + raylib native ANGLE/Direct3D11 build");
SetTargetFPS(60);

while (!WindowShouldClose())
{
    BeginDrawing();

    ClearBackground(Color.RayWhite);
    DrawText("raylib-cs sample", 40, 40, 34, Color.Black);
    DrawText("The C# code is normal raylib-cs.", 40, 95, 22, Color.DarkGray);
    DrawText("Direct3D comes from native raylib.dll -> ANGLE libEGL/libGLESv2 -> D3D11.", 40, 130, 22, Color.DarkBlue);
    DrawText("Verify with Process Explorer / VS Modules: libEGL.dll, libGLESv2.dll, d3d11.dll, dxgi.dll.", 40, 165, 20, Color.DarkGreen);
    DrawRectangle(40, 230, 220, 120, Color.SkyBlue);
    DrawCircle(360, 290, 60, Color.Maroon);
    DrawText($"FPS: {GetFPS()}", 40, 390, 24, Color.DarkGreen);

    EndDrawing();
}

CloseWindow();
